"""Reader for .cast files (Greyhound / Saluki / Husky default export format).

Cast is a tree of typed nodes with named properties. Models become `Model`,
animations become the same keyed-track structure .seanim files use, so both
get baked against a skeleton the same way.
"""
from __future__ import annotations

import os
import struct

from .data import Bone, ConversionError, Face, Material, Model
from .mathutil import q_normalize, v_normalize
from .seanim import (TYPE_ABSOLUTE, TYPE_ADDITIVE, TYPE_RELATIVE, SEAnimData)

MAGIC = 0x74736163
ROOT, MODEL, MESH, SKELETON, BONE = 0x746F6F72, 0x6C646F6D, 0x6873656D, 0x6C656B73, 0x656E6F62
MATERIAL, FILE = 0x6C74616D, 0x656C6966
ANIMATION, CURVE, NOTE_TRACK, MODE_OVERRIDE = 0x6D696E61, 0x76727563, 0x6669746E, 0x564F4D43

_FMT = {"b": "B", "h": "H", "i": "I", "l": "Q", "f": "f", "d": "d",
        "2v": "2f", "3v": "3f", "4v": "4f"}
_WIDTH = {"2v": 2, "3v": 3, "4v": 4}
MODES = {"absolute": TYPE_ABSOLUTE, "relative": TYPE_RELATIVE, "additive": TYPE_ADDITIVE}

# material slot names Cast uses -> our slots
_SLOTS = {"albedo": "color", "diffuse": "color", "color": "color", "basecolor": "color",
          "normal": "normal", "specular": "spec", "gloss": "gloss", "roughness": "gloss",
          "ao": "ao", "occlusion": "ao", "mask": "mask"}


class Node:
    __slots__ = ("id", "hash", "props", "children")

    def __init__(self, nid, nhash):
        self.id = nid
        self.hash = nhash
        self.props = {}
        self.children = []

    def get(self, name, default=None):
        v = self.props.get(name)
        if v is None:
            return default
        return v

    def one(self, name, default=None):
        v = self.props.get(name)
        if v is None or (isinstance(v, (list, tuple)) and not v):
            return default
        return v[0] if isinstance(v, (list, tuple)) else v

    def kids(self, nid):
        return [c for c in self.children if c.id == nid]


def _parse(buf):
    if len(buf) < 16 or struct.unpack_from("<I", buf, 0)[0] != MAGIC:
        raise ConversionError("Not a valid Cast file.")
    _magic, _ver, roots, _flags = struct.unpack_from("<IIII", buf, 0)
    off = 16

    def node(off):
        nid, size, nhash, pcount, ccount = struct.unpack_from("<IIQII", buf, off)
        n = Node(nid, nhash)
        end = off + size
        p = off + 24
        for _ in range(pcount):
            raw_id, name_len, count = struct.unpack_from("<2sHI", buf, p)
            p += 8
            pid = raw_id.rstrip(b"\0").decode("ascii", "replace")
            name = buf[p:p + name_len].decode("utf-8", "replace")
            p += name_len
            if pid == "s":
                z = buf.index(b"\0", p)
                n.props[name] = buf[p:z].decode("utf-8", "replace")
                p = z + 1
                continue
            fmt = _FMT.get(pid)
            if fmt is None:
                raise ConversionError(f"Unknown Cast property type '{pid}'.")
            size1 = struct.calcsize("<" + fmt)
            vals = struct.unpack_from("<" + fmt * count, buf, p)
            p += size1 * count
            w = _WIDTH.get(pid)
            if w:
                vals = [tuple(vals[i:i + w]) for i in range(0, len(vals), w)]
            n.props[name] = list(vals)
        for _ in range(ccount):
            child, p = node(p)
            n.children.append(child)
        return n, max(p, end)

    out = []
    for _ in range(roots):
        n, off = node(off)
        out.append(n)
    return out


def _load(path):
    with open(path, "rb") as f:
        buf = f.read()
    try:
        return _parse(buf)
    except (struct.error, ValueError, IndexError) as e:
        raise ConversionError(f"Cast file is damaged or truncated ({e}).")


def _find(nodes, nid):
    for n in nodes:
        if n.id == nid:
            yield n
        yield from _find(n.children, nid)


def is_animation(path):
    """Cheap check: is the first thing under the root an animation node?"""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
        if struct.unpack_from("<I", head, 0)[0] != MAGIC:
            return False
        off = 16
        _nid, _size, _h, pcount, ccount = struct.unpack_from("<IIQII", head, off)
        p = off + 24
        for _ in range(pcount):           # root properties (rare) - skip them
            raw_id, name_len, count = struct.unpack_from("<2sHI", head, p)
            p += 8 + name_len
            pid = raw_id.rstrip(b"\0").decode("ascii", "replace")
            if pid == "s":
                p = head.index(b"\0", p) + 1
            else:
                p += struct.calcsize("<" + _FMT[pid]) * count
        if ccount:
            return struct.unpack_from("<I", head, p)[0] == ANIMATION
    except Exception:  # noqa: BLE001 - fall back to a full parse
        pass
    return any(True for _ in _find(_load(path), ANIMATION))


def _signed(v):
    v &= 0xFFFFFFFF
    return (v ^ 0x80000000) - 0x80000000


def read_cast_model(path) -> Model:
    roots = _load(path)
    models = list(_find(roots, MODEL))
    if not models:
        raise ConversionError("This Cast file has no model in it (is it an animation?).")
    cm = models[0]
    name = os.path.splitext(os.path.basename(path))[0]
    model = Model(name)

    for skel in cm.kids(SKELETON):
        for bn in skel.kids(BONE):
            b = Bone(bn.get("n", f"bone_{len(model.bones)}"), _signed(bn.one("p", 0xFFFFFFFF)))
            if bn.get("wp") and bn.get("wr"):
                b.world_pos = bn.one("wp")
                b.world_rot = q_normalize(bn.one("wr"))
            if bn.get("lp") and bn.get("lr"):
                b.local_pos = bn.one("lp")
                b.local_rot = q_normalize(bn.one("lr"))
            model.bones.append(b)

    mat_index = {}
    for mn in cm.kids(MATERIAL):
        mat = Material(mn.get("n", f"material_{len(model.materials)}"))
        files = {f.hash: f.get("p", "") for f in mn.kids(FILE)}
        for prop, val in mn.props.items():
            slot = _SLOTS.get(prop.lower())
            if slot and isinstance(val, list) and val and slot not in mat.maps:
                ref = files.get(val[0], "")
                if ref:
                    mat.maps[slot] = ref
        mat_index[mn.hash] = len(model.materials)
        model.materials.append(mat)

    white = (1.0, 1.0, 1.0, 1.0)
    for mi, mesh in enumerate(cm.kids(MESH)):
        pos = mesh.get("vp") or []
        nrm = mesh.get("vn")
        uvs = mesh.get("u0")
        cols = mesh.get("c0") or mesh.get("vc")
        faces = mesh.get("f") or []
        wb, wv = mesh.get("wb"), mesh.get("wv")
        influence = mesh.one("mi", 0) or 0
        mat_hash = mesh.one("m")
        mat = mat_index.get(mat_hash, -1)

        base = len(model.verts)
        for vi, p in enumerate(pos):
            weights = []
            if wb and wv and influence:
                for k in range(influence):
                    j = vi * influence + k
                    if j < len(wv) and wv[j] > 0.0:
                        weights.append((wb[j], wv[j]))
            elif model.bones:
                weights = [(0, 1.0)]
            model.verts.append((p, weights))

        def color(vi):
            if not cols:
                return white
            c = cols[vi]
            if isinstance(c, tuple):
                return c
            return tuple(((c >> s) & 0xFF) / 255.0 for s in (0, 8, 16, 24))

        for k in range(0, len(faces) - 2, 3):
            # Cast stores counter-clockwise triangles; CoD tools want the reverse.
            tri = (faces[k], faces[k + 2], faces[k + 1])
            corners = []
            for vi in tri:
                corners.append((base + vi,
                                v_normalize(nrm[vi]) if nrm else (0.0, 0.0, 1.0),
                                color(vi),
                                uvs[vi] if uvs else (0.0, 0.0)))
            model.faces.append(Face(mi, mat, corners))
        model.objects.append(mesh.get("n") or f"{name}_mesh{mi}")

    bad = [f for f in model.faces if not 0 <= f.mat < len(model.materials)]
    if bad:
        model.materials.append(Material("default"))
        for f in bad:
            f.mat = len(model.materials) - 1
    model.fill_transforms()
    return model


def _sample_scalar(keys, frame):
    """keys: sorted [(frame, value)] -> linearly interpolated value."""
    if not keys:
        return 0.0
    if frame <= keys[0][0]:
        return keys[0][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f0 <= frame <= f1:
            return v0 if f1 == f0 else v0 + (v1 - v0) * (frame - f0) / (f1 - f0)
    return keys[-1][1]


def read_cast_anim(path) -> SEAnimData:
    roots = _load(path)
    anims = list(_find(roots, ANIMATION))
    if not anims:
        raise ConversionError("This Cast file has no animation in it (is it a model?).")
    an = anims[0]
    d = SEAnimData()
    d.framerate = float(an.one("fr", 30.0) or 30.0)
    d.looped = bool(an.one("lo", 0))
    names, per_bone = [], {}
    last = 0
    for cv in an.kids(CURVE):
        bone = cv.get("nn", "")
        prop = cv.get("kp", "")
        frames = cv.get("kb") or []
        values = cv.get("kv") or []
        mode = MODES.get((cv.get("m") or "absolute").lower(), TYPE_ABSOLUTE)
        if bone not in per_bone:
            per_bone[bone] = {"mode": None, "rq": None, "tx": None, "ty": None, "tz": None}
            names.append(bone)
        entry = per_bone[bone]
        if prop in entry:
            entry[prop] = list(zip(frames, values))
            if prop == "rq" or entry["mode"] is None:
                entry["mode"] = mode
        if frames:
            last = max(last, max(frames))
    d.frame_count = last + 1
    d.bone_names = names
    d.bone_types = {}
    for i, bone in enumerate(names):
        e = per_bone[bone]
        track = {}
        if e["rq"]:
            track["rot"] = sorted(((f, q_normalize(tuple(q))) for f, q in e["rq"]), key=lambda kv: kv[0])
        axes = [sorted(e[k], key=lambda kv: kv[0]) if e[k] else None for k in ("tx", "ty", "tz")]
        if any(axes):
            frames = sorted({f for ax in axes if ax for f, _ in ax})
            track["loc"] = [(f, tuple(_sample_scalar(ax, f) if ax else None for ax in axes))
                            for f in frames]
        d.tracks.append(track)
        d.bone_types[i] = e["mode"] if e["mode"] is not None else TYPE_ABSOLUTE
    for ov in an.kids(MODE_OVERRIDE):
        bone = ov.get("nn", "")
        if bone in per_bone:
            d.modifiers[names.index(bone)] = MODES.get((ov.get("m") or "").lower(), TYPE_ABSOLUTE)
    types = set(d.bone_types.values())
    d.anim_type = types.pop() if len(types) == 1 else TYPE_RELATIVE
    for nt in an.kids(NOTE_TRACK):
        for f in nt.get("kb") or []:
            d.notes.append((f, nt.get("n", "note")))
    return d
