"""Reader for .semodel files (exported by Greyhound, Wraith, Legion, etc.)."""
from __future__ import annotations

import os
import struct

from .data import Bone, ConversionError, Face, Material, Model
from .mathutil import q_normalize, v_normalize

_PRESENCE_BONE, _PRESENCE_MESH, _PRESENCE_MATERIALS = 1, 2, 4
_BONE_GLOBAL, _BONE_LOCAL, _BONE_SCALES = 1, 2, 4
_MESH_UV, _MESH_NORMALS, _MESH_COLOR, _MESH_WEIGHTS = 1, 2, 4, 8


def _index_fmt(count):
    return "B" if count <= 0xFF else ("H" if count <= 0xFFFF else "I")


def _cstr(buf, off):
    end = buf.index(b"\0", off)
    return buf[off:end].decode("utf-8", "replace"), end + 1


def read_semodel(path) -> Model:
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:7] != b"SEModel":
        raise ConversionError("Not a valid SEModel file.")
    try:
        return _parse(buf, os.path.splitext(os.path.basename(path))[0])
    except (struct.error, ValueError, IndexError) as e:
        raise ConversionError(f"SEModel file is damaged or truncated ({e}).")


def _parse(buf, name) -> Model:
    off = 9  # magic (7) + version (2)
    header_size, = struct.unpack_from("<H", buf, off)
    dflags, bflags, mflags, bone_count, mesh_count, mat_count = \
        struct.unpack_from("<3BIII", buf, off + 2)
    off += header_size

    model = Model(name)

    if dflags & _PRESENCE_BONE:
        names = []
        for _ in range(bone_count):
            n, off = _cstr(buf, off)
            names.append(n)
        for i in range(bone_count):
            _flags, parent = struct.unpack_from("<Bi", buf, off)
            off += 5
            b = Bone(names[i], parent)
            if bflags & _BONE_GLOBAL:
                v = struct.unpack_from("<7f", buf, off)
                off += 28
                b.world_pos, b.world_rot = v[:3], q_normalize(v[3:])
            if bflags & _BONE_LOCAL:
                v = struct.unpack_from("<7f", buf, off)
                off += 28
                b.local_pos, b.local_rot = v[:3], q_normalize(v[3:])
            if bflags & _BONE_SCALES:
                off += 12
            model.bones.append(b)
    else:
        bone_count = 0

    bfmt = _index_fmt(bone_count)
    mesh_mats = []
    if dflags & _PRESENCE_MESH:
        for mi in range(mesh_count):
            _flags, layers, max_inf, vcount, fcount = \
                struct.unpack_from("<3BII", buf, off)
            off += 11
            if not mflags & _MESH_UV:
                layers = 0
            if not mflags & _MESH_WEIGHTS:
                max_inf = 0

            size = 12 * vcount
            positions = list(struct.iter_unpack("<3f", buf[off:off + size]))
            off += size

            uvs = None
            if layers:
                stride = 8 * layers
                raw = buf[off:off + stride * vcount]
                uvs = [struct.unpack_from("<2f", raw, i * stride)
                       for i in range(vcount)]
                off += stride * vcount

            normals = None
            if mflags & _MESH_NORMALS:
                size = 12 * vcount
                normals = list(struct.iter_unpack("<3f", buf[off:off + size]))
                off += size

            colors = None
            if mflags & _MESH_COLOR:
                size = 4 * vcount
                colors = [(r / 255.0, g / 255.0, b / 255.0, a / 255.0) for r, g, b, a
                          in struct.iter_unpack("<4B", buf[off:off + size])]
                off += size

            weights = None
            if max_inf:
                rec = "<" + (bfmt + "f") * max_inf
                rsize = struct.calcsize(rec)
                weights = []
                for vals in struct.iter_unpack(rec, buf[off:off + rsize * vcount]):
                    weights.append([(vals[k], vals[k + 1])
                                    for k in range(0, len(vals), 2)
                                    if vals[k + 1] > 0.0])
                off += rsize * vcount

            ffmt = _index_fmt(vcount)
            size = 3 * struct.calcsize(ffmt) * fcount
            faces = list(struct.iter_unpack("<3" + ffmt, buf[off:off + size]))
            off += size

            refs = struct.unpack_from("<%di" % layers, buf, off) if layers else ()
            off += 4 * layers
            mesh_mats.append(refs[0] if refs else -1)

            base = len(model.verts)
            for vi in range(vcount):
                model.verts.append((positions[vi], weights[vi] if weights else []))
            white = (1.0, 1.0, 1.0, 1.0)
            for a, b, c in faces:
                corners = []
                for vi in (a, b, c):
                    corners.append((
                        base + vi,
                        v_normalize(normals[vi]) if normals else (0.0, 0.0, 1.0),
                        colors[vi] if colors else white,
                        uvs[vi] if uvs else (0.0, 0.0),
                    ))
                model.faces.append(Face(mi, mesh_mats[-1], corners))
            model.objects.append(f"{name}_mesh{mi}")

    if dflags & _PRESENCE_MATERIALS:
        for _ in range(mat_count):
            mname, off = _cstr(buf, off)
            simple = buf[off]
            off += 1
            mat = Material(mname)
            if simple:
                for slot in ("color", "normal", "spec"):
                    ref, off = _cstr(buf, off)
                    if ref:
                        mat.maps[slot] = ref
            model.materials.append(mat)

    # Faces that point at a missing material share one default material.
    bad = [f for f in model.faces if not 0 <= f.mat < len(model.materials)]
    if bad:
        model.materials.append(Material("default"))
        for f in bad:
            f.mat = len(model.materials) - 1

    model.fill_transforms()
    return model
