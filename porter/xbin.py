"""Reader and writer for BO3's binary .xmodel_bin / .xanim_bin files.

These hold the same data as xmodel_export / xanim_export, stored as a stream
of small tagged blocks and LZ4-compressed. The block IDs below are the ones
BO3's Export2Bin tool writes (documented by the open-source PyCoD project).
"""
from __future__ import annotations

import os
import struct

from . import lz4block
from .data import Anim, Bone, ConversionError, Face, Material, Model
from .mathutil import axes_to_q, q_to_axes, v_normalize

MAGIC = b"*LZ4*"

# block ids
COMMENT, MODEL, ANIM, VERSION = 0xC355, 0x46C8, 0x7AAC, 0x24D1
BONE_COUNT, COSMETICS, BONE_INFO, BONE_INDEX = 0x76BA, 0x7836, 0xF099, 0xDD9A
OFFSET, SCALE, MAT_X, MAT_Y, MAT_Z = 0x9383, 0x1C56, 0xDCFD, 0xCCDC, 0xFCBF
VERT_COUNT, VERT32_COUNT, VERT_INDEX, VERT32_INDEX = 0x950D, 0x2AEC, 0x8F03, 0xB097
WEIGHT_COUNT, WEIGHT = 0xEA46, 0xF1AB
TRI_COUNT, TRI, TRI16 = 0xBE92, 0x562F, 0x6711
NORMAL, COLOR, UV = 0x89EC, 0x6DD8, 0x1AD4
OBJ_COUNT, OBJ_INFO, MAT_COUNT, MAT_INFO = 0x62AF, 0x87D4, 0xA1B2, 0xA700
MAT_VEC4 = {0x6DAB, 0x37FF, 0x4265, 0x317C, 0xE593}
MAT_VEC2 = {0xC835, 0xFE0C, 0x7E24, 0x7D76, 0x83C7}
MAT_FLOAT = {0x5CD2}
PART_COUNT, PART_INFO, PART_INDEX = 0x9279, 0x360B, 0x745A
FRAMERATE, FRAME_COUNT, FRAME = 0x92D3, 0xB917, 0xC723
NOTE_SECTION, NUM_TRACKS, NUM_KEYS, NOTETRACK, NOTE_FRAME = 0xC7F3, 0x9016, 0x7A6C, 0x4643, 0x1675
EXTRA, FIRSTFRAME = 0x6EEE, 0xBCD4
UNSUPPORTED = {0x1FC2: "siege models", 0xB35E: "siege weights", 0xEF69: "quaternion blocks",
               0xA65B: "IK pitch layers", 0x1D7D: "IK pitch layers", 0xA58B: "rotation blocks"}


def _pad(n):
    return (n + 3) & ~3


# --------------------------------------------------------------------- reading
class _Stream:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def cstr(self, at):
        end = self.d.index(b"\0", at)
        return self.d[at:end].decode("utf-8", "replace"), end + 1


def _load(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:5] != MAGIC:
        raise ConversionError("Not a valid BO3 binary file (missing *LZ4* header).")
    size, = struct.unpack_from("<I", raw, 5)
    try:
        return lz4block.decompress(raw[9:], size)
    except (ValueError, IndexError) as e:
        raise ConversionError(f"Couldn't decompress this file ({e}).")


def _blocks(data):
    """Yield (block_id, payload_offset) and advance past each block."""
    s = _Stream(data)
    n = len(data)
    unpack = struct.unpack_from
    while s.p + 2 <= n:
        start = s.p
        bid, = unpack("<H", data, start)
        p = start + 2
        yield bid, p, s
        # caller sets s.p for variable blocks; otherwise use the fixed size
        if s.p == start:
            raise ConversionError(f"Unknown block 0x{bid:04X} at byte {start}.")


def read_xmodel_bin(path) -> Model:
    data = _load(path)
    model = Model(os.path.splitext(os.path.basename(path))[0])
    unpack = struct.unpack_from
    bone_axes = {}
    cur_bone = None
    cur_vert = None
    cur_face = None
    corner = None
    in_faces = False
    cur_mat = None
    is_model = False

    def finish_corner():
        if cur_face is not None and corner is not None:
            cur_face.corners.append(tuple(corner))

    try:
        for bid, p, s in _blocks(data):
            start = p - 2
            if bid == COMMENT:
                _, end = s.cstr(p + 2)
                s.p = start + _pad(end - start)
            elif bid in (MODEL, ANIM):
                if bid == ANIM:
                    raise ConversionError("This is an animation file, not a model.")
                is_model = True
                s.p = p + 2
            elif bid == VERSION:
                s.p = p + 2
            elif bid == BONE_COUNT:
                model.bones = [None] * unpack("<h", data, p)[0]
                s.p = p + 2
            elif bid == COSMETICS:
                s.p = p + 6
            elif bid == BONE_INFO:
                idx, parent = unpack("<ii", data, p + 2)
                name, end = s.cstr(p + 10)
                model.bones[idx] = Bone(name, parent)
                s.p = start + _pad(end - start)
            elif bid == BONE_INDEX:
                cur_bone = unpack("<h", data, p)[0]
                bone_axes[cur_bone] = {}
                cur_vert = None
                s.p = p + 2
            elif bid == OFFSET:
                v = unpack("<3f", data, p + 2)
                if cur_vert is not None:
                    model.verts[cur_vert][0] = v
                elif cur_bone is not None:
                    bone_axes[cur_bone]["OFFSET"] = v
                s.p = p + 14
            elif bid == SCALE:
                s.p = p + 14
            elif bid in (MAT_X, MAT_Y, MAT_Z):
                v = tuple(c / 32767.0 for c in unpack("<3h", data, p))
                if cur_bone is not None:
                    bone_axes[cur_bone]["XYZ"[(MAT_X, MAT_Y, MAT_Z).index(bid)]] = v
                s.p = p + 6
            elif bid == VERT_COUNT:
                model.verts = [[(0.0, 0.0, 0.0), []] for _ in range(unpack("<H", data, p)[0])]
                cur_bone = None
                s.p = p + 2
            elif bid == VERT32_COUNT:
                model.verts = [[(0.0, 0.0, 0.0), []] for _ in range(unpack("<I", data, p + 2)[0])]
                cur_bone = None
                s.p = p + 6
            elif bid in (VERT_INDEX, VERT32_INDEX):
                if bid == VERT_INDEX:
                    idx = unpack("<H", data, p)[0]
                    s.p = p + 2
                else:
                    idx = unpack("<I", data, p + 2)[0]
                    s.p = p + 6
                if in_faces:
                    finish_corner()
                    corner = [idx, (0.0, 0.0, 1.0), (1.0, 1.0, 1.0, 1.0), (0.0, 0.0)]
                else:
                    cur_vert = idx
            elif bid == WEIGHT_COUNT:
                s.p = p + 2
            elif bid == WEIGHT:
                b, w = unpack("<hf", data, p)
                model.verts[cur_vert][1].append((b, w))
                s.p = p + 6
            elif bid == TRI_COUNT:
                in_faces = True
                cur_vert = None
                s.p = p + 6
            elif bid in (TRI, TRI16):
                finish_corner()
                corner = None
                if bid == TRI:
                    obj, mat = unpack("<BB", data, p)
                    s.p = p + 2
                else:
                    obj, mat = unpack("<HH", data, p + 2)
                    s.p = p + 6
                cur_face = Face(obj, mat, [])
                model.faces.append(cur_face)
            elif bid == NORMAL:
                corner[1] = v_normalize(tuple(c / 32767.0 for c in unpack("<3h", data, p)))
                s.p = p + 6
            elif bid == COLOR:
                rgba = tuple(c / 255.0 for c in unpack("<4B", data, p + 2))
                if corner is not None and cur_mat is None:
                    corner[2] = rgba
                s.p = p + 6
            elif bid == UV:
                layers = unpack("<h", data, p)[0]
                if corner is not None and layers > 0:
                    corner[3] = unpack("<2f", data, p + 2)
                s.p = p + 2 + 8 * max(0, layers)
            elif bid == OBJ_COUNT:
                finish_corner()
                corner = None
                in_faces = False
                model.objects = [""] * unpack("<h", data, p)[0]
                s.p = p + 2
            elif bid == OBJ_INFO:
                idx = unpack("<h", data, p)[0]
                name, end = s.cstr(p + 2)
                model.objects[idx] = name
                s.p = start + _pad(end - start)
            elif bid == MAT_COUNT:
                model.materials = [None] * unpack("<h", data, p)[0]
                s.p = p + 2
            elif bid == MAT_INFO:
                idx = unpack("<h", data, p)[0]
                q = p + 2
                strings = []
                for _ in range(3):
                    text, end = s.cstr(q)
                    strings.append(text)
                    q = q + _pad(end - q)
                mat = Material(strings[0])
                mat.maps.update(parse_image_string(strings[2]))
                model.materials[idx] = mat
                cur_mat = mat
                s.p = start + _pad(q - start)
            elif bid in MAT_VEC4:
                s.p = p + 18
            elif bid in MAT_VEC2:
                s.p = p + 10
            elif bid in MAT_FLOAT:
                s.p = p + 6
            elif bid in UNSUPPORTED:
                raise ConversionError(f"This file uses {UNSUPPORTED[bid]}, which isn't supported.")
            else:
                raise ConversionError(f"Unknown block 0x{bid:04X} in this file.")
    except (struct.error, IndexError, TypeError, ValueError) as e:
        raise ConversionError(f"xmodel_bin file looks damaged ({e}).")

    finish_corner()
    if not is_model:
        raise ConversionError("Not a model file.")
    model.verts = [(tuple(v[0]), v[1]) for v in model.verts]
    for i, b in enumerate(model.bones):
        if b is None:
            model.bones[i] = b = Bone(f"bone_{i}", -1 if i == 0 else 0)
        ax = bone_axes.get(i, {})
        b.world_pos = ax.get("OFFSET", (0.0, 0.0, 0.0))
        b.world_rot = axes_to_q(ax["X"], ax["Y"], ax["Z"]) if all(k in ax for k in "XYZ") \
            else (0.0, 0.0, 0.0, 1.0)
    model.faces = [f for f in model.faces if len(f.corners) == 3]
    model.objects = [o or f"{model.name}_{i}" for i, o in enumerate(model.objects)] or [model.name]
    model.materials = [m or Material(f"material_{i}") for i, m in enumerate(model.materials)]
    model.fill_transforms()
    return model


def read_xanim_bin(path) -> Anim:
    data = _load(path)
    anim = Anim(os.path.splitext(os.path.basename(path))[0])
    unpack = struct.unpack_from
    ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    frame = part = None
    frame_numbers = []
    is_anim = False
    try:
        for bid, p, s in _blocks(data):
            start = p - 2
            if bid == COMMENT:
                _, end = s.cstr(p + 2)
                s.p = start + _pad(end - start)
            elif bid in (MODEL, ANIM):
                if bid == MODEL:
                    raise ConversionError("This is a model file, not an animation.")
                is_anim = True
                s.p = p + 2
            elif bid == VERSION:
                s.p = p + 2
            elif bid == PART_COUNT:
                anim.parts = [""] * unpack("<h", data, p)[0]
                s.p = p + 2
            elif bid == PART_INFO:
                idx = unpack("<h", data, p)[0]
                name, end = s.cstr(p + 2)
                anim.parts[idx] = name
                s.p = start + _pad(end - start)
            elif bid == FRAMERATE:
                anim.framerate = float(unpack("<h", data, p)[0])
                s.p = p + 2
            elif bid in (FRAME_COUNT, FIRSTFRAME):
                s.p = p + 6
            elif bid == FRAME:
                frame = [[(0.0, 0.0, 0.0), *ident] for _ in anim.parts]
                anim.frames.append(frame)
                frame_numbers.append(unpack("<i", data, p + 2)[0])
                s.p = p + 6
            elif bid == PART_INDEX:
                part = frame[unpack("<h", data, p)[0]]
                s.p = p + 2
            elif bid == OFFSET:
                part[0] = unpack("<3f", data, p + 2)
                s.p = p + 14
            elif bid == SCALE:
                s.p = p + 14
            elif bid in (MAT_X, MAT_Y, MAT_Z):
                part[(MAT_X, MAT_Y, MAT_Z).index(bid) + 1] = \
                    tuple(c / 32767.0 for c in unpack("<3h", data, p))
                s.p = p + 6
            elif bid in (NOTE_SECTION, NUM_TRACKS, NUM_KEYS, NOTETRACK):
                s.p = p + 2
            elif bid == NOTE_FRAME:
                fr = unpack("<i", data, p + 2)[0]
                name, end = s.cstr(p + 6)
                anim.notes.append((fr, name))
                s.p = start + _pad(end - start)
            elif bid == EXTRA:
                s.p = p + 18
            elif bid in UNSUPPORTED:
                raise ConversionError(f"This file uses {UNSUPPORTED[bid]}, which isn't supported.")
            else:
                raise ConversionError(f"Unknown block 0x{bid:04X} in this file.")
    except (struct.error, IndexError, TypeError, ValueError) as e:
        raise ConversionError(f"xanim_bin file looks damaged ({e}).")
    if not is_anim or not anim.parts:
        raise ConversionError("Not an animation file.")
    first = min(frame_numbers) if frame_numbers else 0
    order = sorted(range(len(anim.frames)), key=lambda i: frame_numbers[i])
    anim.frames = [[tuple(pt) for pt in anim.frames[i]] for i in order]
    anim.notes = [(max(0, f - first), n) for f, n in anim.notes]
    anim.first_frame = first
    return anim


# --------------------------------------------------------------------- writing
def parse_image_string(images):
    """'color:a.tif normal:b.tif' or a bare 'a.tif' -> {slot: file}."""
    images = (images or "").strip()
    if not images:
        return {}
    out = {}
    if ":" in images and not (len(images) > 1 and images[1] == ":" and " " not in images):
        for part in images.split():
            k, sep, v = part.partition(":")
            if sep and v:
                k = k.lower()
                slot = {"colour": "color", "diffuse": "color", "albedo": "color",
                        "specular": "spec", "specular color": "spec",
                        "cosinepower": "gloss"}.get(k, k)
                out[slot] = v
        if out:
            return out
    return {"color": images}


def image_string(mat, ext_style=True):
    files = mat.outputs or {}
    if not files:
        return mat.maps.get("color", "")
    if not ext_style:
        return files.get("color", next(iter(files.values())))
    return " ".join(f"{k}:{v}" for k, v in files.items())


def _clamp_short(v):
    return max(-32768, min(32767, int(round(v * 32767.0))))


def _cstr_block(prefix: bytes, text: str, start_len: int):
    raw = text.encode("utf-8") + b"\0"
    body = prefix + raw
    return body + b"\0" * (_pad(len(body)) - len(body))


def write_xmodel_bin(model: Model, path, comment="Converted by CoD Asset Porter"):
    out = bytearray()
    pack = struct.pack
    out += _cstr_block(pack("<Hxx", COMMENT), comment, 0)
    out += pack("<Hxx", MODEL)
    out += pack("<Hh", VERSION, 7)
    out += pack("<Hh", BONE_COUNT, len(model.bones))
    for i, b in enumerate(model.bones):
        out += _cstr_block(pack("<Hxxii", BONE_INFO, i, b.parent), b.name, 0)
    for i, b in enumerate(model.bones):
        X, Y, Z = q_to_axes(b.world_rot)
        out += pack("<Hh", BONE_INDEX, i)
        out += pack("<Hxx3f", OFFSET, *b.world_pos)
        out += pack("<Hxx3f", SCALE, 1.0, 1.0, 1.0)
        out += pack("<H3h", MAT_X, *map(_clamp_short, X))
        out += pack("<H3h", MAT_Y, *map(_clamp_short, Y))
        out += pack("<H3h", MAT_Z, *map(_clamp_short, Z))

    big = len(model.verts) > 0xFFFF
    if big:
        out += pack("<HxxI", VERT32_COUNT, len(model.verts))
        vidx = lambda i: pack("<HxxI", VERT32_INDEX, i)  # noqa: E731
    else:
        out += pack("<HH", VERT_COUNT, len(model.verts))
        vidx = lambda i: pack("<HH", VERT_INDEX, i)  # noqa: E731
    for i, (pos, weights) in enumerate(model.verts):
        out += vidx(i)
        out += pack("<Hxx3f", OFFSET, *pos)
        out += pack("<Hh", WEIGHT_COUNT, len(weights))
        for bi, w in weights:
            out += pack("<Hhf", WEIGHT, bi, w)

    out += pack("<HxxI", TRI_COUNT, len(model.faces))
    for f in model.faces:
        if f.obj > 255 or f.mat > 255:
            out += pack("<HxxHH", TRI16, f.obj, f.mat)
        else:
            out += pack("<HBB", TRI, f.obj, f.mat)
        for vi, n, c, uv in f.corners:
            out += vidx(vi)
            out += pack("<H3h", NORMAL, *map(_clamp_short, n))
            out += pack("<Hxx4B", COLOR, *(max(0, min(255, int(round(x * 255)))) for x in c))
            out += pack("<Hhff", UV, 1, uv[0], uv[1])

    out += pack("<Hh", OBJ_COUNT, len(model.objects))
    for i, name in enumerate(model.objects):
        out += _cstr_block(pack("<Hh", OBJ_INFO, i), name, 0)

    out += pack("<Hh", MAT_COUNT, len(model.materials))
    for i, m in enumerate(model.materials):
        body = bytearray(pack("<Hh", MAT_INFO, i))
        for text in (m.name, "lambert", image_string(m)):
            raw = text.encode("utf-8") + b"\0"
            body += raw + b"\0" * (_pad(len(raw)) - len(raw))
        out += body
        out += pack("<Hxx4B", COLOR, 0, 0, 0, 255)
        out += pack("<Hxx4f", 0x6DAB, 0.0, 0.0, 0.0, 1.0)
        out += pack("<Hxx4f", 0x37FF, 0.0, 0.0, 0.0, 1.0)
        out += pack("<Hxx4f", 0x4265, 0.0, 0.0, 0.0, 1.0)
        out += pack("<Hxx2f", 0xC835, 0.8, 0.0)
        out += pack("<Hxx2f", 0xFE0C, 0.0, 0.0)
        out += pack("<Hxx2f", 0x7E24, 6.0, 1.0)
        out += pack("<Hxx4f", 0x317C, -1.0, -1.0, -1.0, 1.0)
        out += pack("<Hxx4f", 0xE593, -1.0, -1.0, -1.0, 1.0)
        out += pack("<Hxx2f", 0x7D76, -1.0, -1.0)
        out += pack("<Hxx2f", 0x83C7, -1.0, -1.0)
        out += pack("<Hxxf", 0x5CD2, -1.0)
    _save(path, out)


def write_xanim_bin(anim: Anim, path, comment="Converted by CoD Asset Porter"):
    out = bytearray()
    pack = struct.pack
    out += _cstr_block(pack("<Hxx", COMMENT), comment, 0)
    out += pack("<Hxx", ANIM)
    out += pack("<Hh", VERSION, 3)
    out += pack("<Hh", PART_COUNT, len(anim.parts))
    for i, name in enumerate(anim.parts):
        out += _cstr_block(pack("<Hh", PART_INFO, i), name, 0)
    out += pack("<Hh", FRAMERATE, int(round(anim.framerate)))
    out += pack("<Hxxi", FRAME_COUNT, len(anim.frames))
    for fi, frame in enumerate(anim.frames):
        out += pack("<Hxxi", FRAME, fi)
        for pi, (off, X, Y, Z) in enumerate(frame):
            out += pack("<Hh", PART_INDEX, pi)
            out += pack("<Hxx3f", OFFSET, *off)
            out += pack("<H3h", MAT_X, *map(_clamp_short, X))
            out += pack("<H3h", MAT_Y, *map(_clamp_short, Y))
            out += pack("<H3h", MAT_Z, *map(_clamp_short, Z))
    last = max(0, len(anim.frames) - 1)
    notes = sorted((min(max(0, int(f)), last), n) for f, n in anim.notes)
    out += pack("<Hh", NUM_KEYS, len(notes))
    for f, n in notes:
        out += _cstr_block(pack("<Hxxi", NOTE_FRAME, f), n, 0)
    _save(path, out)


def _save(path, data):
    blob = lz4block.compress(bytes(data))
    with open(path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", len(data)))
        f.write(blob)
