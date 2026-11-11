"""Binary glTF (.glb) writer for Black Ops 2 via OpenAssetTools.

Follows the conventions OpenAssetTools uses for its own glTF files (xmodel
JSON version 2): CoD's Z-up space is turned into glTF's Y-up space by a -90
degree turn around X, triangles are flipped, UVs are kept as-is, and bones
are written as a node hierarchy with a skin.
"""
from __future__ import annotations

import json
import math
import struct

from .data import Model
from .mathutil import q_conj, q_mul, q_normalize, q_rotate, v_sub

_S = math.sqrt(0.5)
TO_GLTF = (-_S, 0.0, 0.0, _S)   # -90 degrees about X, (x, y, z, w)


def _pos(v):
    return (v[0], v[2], -v[1])


def _quat(q):
    return q_normalize(q_mul(TO_GLTF, q))


def _mat4(pos, rot):
    """Column-major 4x4 for translation * rotation."""
    x, y, z, w = rot
    m = [1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y), 0.0,
         2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x), 0.0,
         2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y), 0.0,
         pos[0], pos[1], pos[2], 1.0]
    return m


def _inverse_rigid(pos, rot):
    inv = q_conj(rot)
    t = q_rotate(inv, (-pos[0], -pos[1], -pos[2]))
    return _mat4(t, inv)


def write_glb(model: Model, path):
    bones = model.bones
    gpos = [_pos(b.world_pos) for b in bones]
    grot = [_quat(b.world_rot) for b in bones]

    # Split face corners into unique vertices per material.
    prims = {}
    for f in model.faces:
        prims.setdefault(f.mat, []).append(f)

    bin_parts = []
    offset = 0
    buffer_views, accessors = [], []

    def add_view(data: bytes, target=None):
        nonlocal offset
        pad = (-offset) % 4
        if pad:
            bin_parts.append(b"\0" * pad)
            offset += pad
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        buffer_views.append(view)
        bin_parts.append(data)
        offset += len(data)
        return len(buffer_views) - 1

    def add_accessor(view, ctype, count, atype, **extra):
        acc = {"bufferView": view, "componentType": ctype, "count": count, "type": atype}
        acc.update(extra)
        accessors.append(acc)
        return len(accessors) - 1

    joint_fmt, joint_type = ("B", 5121) if len(bones) <= 256 else ("H", 5123)
    primitives = []
    for mat, faces in sorted(prims.items()):
        verts, lookup, indices = [], {}, []
        for f in faces:
            tri = []
            for vi, n, _c, uv in f.corners:
                key = (vi, round(n[0], 4), round(n[1], 4), round(n[2], 4),
                       round(uv[0], 6), round(uv[1], 6))
                idx = lookup.get(key)
                if idx is None:
                    idx = len(verts)
                    lookup[key] = idx
                    verts.append((vi, n, uv))
                tri.append(idx)
            indices.extend((tri[2], tri[1], tri[0]))   # glTF winding
        pos = [_pos(model.verts[vi][0]) for vi, _, _ in verts]
        nrm = [_pos(n) for _, n, _ in verts]
        mins = [min(p[k] for p in pos) for k in range(3)]
        maxs = [max(p[k] for p in pos) for k in range(3)]
        attrs = {
            "POSITION": add_accessor(add_view(struct.pack("<%df" % (3 * len(pos)), *[c for p in pos for c in p]), 34962),
                                     5126, len(pos), "VEC3", min=mins, max=maxs),
            "NORMAL": add_accessor(add_view(struct.pack("<%df" % (3 * len(nrm)), *[c for p in nrm for c in p]), 34962),
                                   5126, len(nrm), "VEC3"),
            "TEXCOORD_0": add_accessor(add_view(struct.pack("<%df" % (2 * len(verts)), *[c for _, _, uv in verts for c in uv]), 34962),
                                       5126, len(verts), "VEC2"),
        }
        if bones:
            js, ws = [], []
            for vi, _, _ in verts:
                weights = sorted(model.verts[vi][1], key=lambda bw: -bw[1])[:4] or [(0, 1.0)]
                total = sum(w for _, w in weights) or 1.0
                weights = [(b, w / total) for b, w in weights] + [(0, 0.0)] * (4 - len(weights))
                js.extend(b for b, _ in weights)
                ws.extend(w for _, w in weights)
            attrs["JOINTS_0"] = add_accessor(add_view(struct.pack("<%d%s" % (len(js), joint_fmt), *js), 34962),
                                             joint_type, len(verts), "VEC4")
            attrs["WEIGHTS_0"] = add_accessor(add_view(struct.pack("<%df" % len(ws), *ws), 34962),
                                              5126, len(verts), "VEC4")
        if len(verts) > 0xFFFF:
            idx_acc = add_accessor(add_view(struct.pack("<%dI" % len(indices), *indices), 34963),
                                   5125, len(indices), "SCALAR")
        else:
            idx_acc = add_accessor(add_view(struct.pack("<%dH" % len(indices), *indices), 34963),
                                   5123, len(indices), "SCALAR")
        primitives.append({"attributes": attrs, "indices": idx_acc, "material": mat})

    nodes = [{"name": model.name, "mesh": 0}]
    if bones:
        nodes[0]["skin"] = 0
    first_bone = len(nodes)
    roots = []
    for i, b in enumerate(bones):
        if b.parent >= 0:
            inv = q_conj(grot[b.parent])
            t = q_rotate(inv, v_sub(gpos[i], gpos[b.parent]))
            r = q_normalize(q_mul(inv, grot[i]))
        else:
            t, r = gpos[i], grot[i]
            roots.append(first_bone + i)
        node = {"name": b.name, "translation": list(t), "rotation": list(r)}
        kids = [first_bone + j for j, c in enumerate(bones) if c.parent == i]
        if kids:
            node["children"] = kids
        nodes.append(node)
    root = {"name": f"{model.name}_skel", "children": [0] + roots}
    nodes.append(root)

    gltf = {
        "asset": {"version": "2.0", "generator": "CoD Asset Porter"},
        "scene": 0,
        "scenes": [{"nodes": [len(nodes) - 1]}],
        "nodes": nodes,
        "meshes": [{"name": model.name, "primitives": primitives}],
        "materials": [{"name": m.name} for m in model.materials],
        "buffers": None,
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    if bones:
        ibm = []
        for i in range(len(bones)):
            ibm.extend(_inverse_rigid(gpos[i], grot[i]))
        ibm_acc = add_accessor(add_view(struct.pack("<%df" % len(ibm), *ibm)), 5126, len(bones), "MAT4")
        gltf["skins"] = [{"inverseBindMatrices": ibm_acc,
                          "joints": [first_bone + i for i in range(len(bones))],
                          "skeleton": roots[0] if roots else first_bone}]
    blob = b"".join(bin_parts)
    blob += b"\0" * ((-len(blob)) % 4)
    gltf["buffers"] = [{"byteLength": len(blob)}]

    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(js), b"JSON"))
        f.write(js)
        f.write(struct.pack("<I4s", len(blob), b"BIN\0"))
        f.write(blob)


def write_oat_xmodel_json(path, model_name, glb_rel, model_type):
    data = {
        "_type": "xmodel",
        "_version": 2,
        "type": model_type,
        "lods": [{"file": glb_rel, "distance": 0.0}],
        "flags": 0,
        "lightingOriginOffset": {"x": 0.0, "y": 0.0, "z": 0.0},
        "lightingOriginRange": 0.0,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
