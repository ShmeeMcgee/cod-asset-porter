"""Reader for .seanim files (keyframed animation exported by Greyhound etc.)."""
from __future__ import annotations

import struct

from .data import ConversionError
from .mathutil import q_normalize

TYPE_ABSOLUTE, TYPE_ADDITIVE, TYPE_RELATIVE, TYPE_DELTA = 0, 1, 2, 3
TYPE_NAMES = {0: "absolute", 1: "additive", 2: "relative", 3: "delta"}

_LOC, _ROT, _SCALE, _NOTE = 1, 2, 4, 64


def _index_fmt(count):
    return "B" if count <= 0xFF else ("H" if count <= 0xFFFF else "I")


class SEAnimData:
    def __init__(self):
        self.anim_type = TYPE_ABSOLUTE
        self.looped = False
        self.framerate = 30.0
        self.frame_count = 1
        self.bone_names: list[str] = []
        self.modifiers: dict[int, int] = {}      # bone index -> anim type (+ children)
        self.bone_types: dict[int, int] = {}     # bone index -> anim type (that bone only)
        # per bone: {"loc": [(frame, vec3)], "rot": [(frame, quat)], ...}
        self.tracks: list[dict] = []
        self.notes: list = []                    # (frame, name)


def read_seanim(path) -> SEAnimData:
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:6] != b"SEAnim":
        raise ConversionError("Not a valid SEAnim file.")
    try:
        return _parse(buf)
    except (struct.error, ValueError, IndexError) as e:
        raise ConversionError(f"SEAnim file is damaged or truncated ({e}).")


def _parse(buf) -> SEAnimData:
    off = 8  # magic (6) + version (2)
    (header_size, anim_type, anim_flags, presence, props, _r1,
     framerate, frame_count, bone_count, mod_count, _r2, note_count) = \
        struct.unpack_from("<HBBBBHfIIB3sI", buf, off)
    off += header_size

    d = SEAnimData()
    d.anim_type = anim_type
    d.looped = bool(anim_flags & 1)
    d.framerate = framerate if framerate > 0 else 30.0
    d.frame_count = max(1, frame_count)

    ffmt = _index_fmt(frame_count)
    fsize = struct.calcsize(ffmt)
    bfmt = _index_fmt(bone_count)
    fl = "d" if props & 1 else "f"
    vec_rec = "<" + ffmt + fl * 3
    quat_rec = "<" + ffmt + fl * 4

    def read_keys(rec, offset):
        count, = struct.unpack_from("<" + ffmt, buf, offset)
        offset += fsize
        size = struct.calcsize(rec)
        keys = []
        for vals in struct.iter_unpack(rec, buf[offset:offset + size * count]):
            keys.append((vals[0], tuple(vals[1:])))
        return keys, offset + size * count

    if presence & (_LOC | _ROT | _SCALE):
        for _ in range(bone_count):
            end = buf.index(b"\0", off)
            d.bone_names.append(buf[off:end].decode("utf-8", "replace"))
            off = end + 1
        mod_rec = "<" + bfmt + "B"
        for _ in range(mod_count):
            idx, t = struct.unpack_from(mod_rec, buf, off)
            off += struct.calcsize(mod_rec)
            d.modifiers[idx] = t
        for _ in range(bone_count):
            off += 1  # bone flags
            track = {}
            if presence & _LOC:
                track["loc"], off = read_keys(vec_rec, off)
            if presence & _ROT:
                keys, off = read_keys(quat_rec, off)
                track["rot"] = [(fr, q_normalize(q)) for fr, q in keys]
            if presence & _SCALE:
                track["scale"], off = read_keys(vec_rec, off)
            for k in track.values():
                k.sort(key=lambda kv: kv[0])
            d.tracks.append(track)

    if presence & _NOTE:
        for _ in range(note_count):
            frame, = struct.unpack_from("<" + ffmt, buf, off)
            off += fsize
            end = buf.index(b"\0", off)
            d.notes.append((frame, buf[off:end].decode("utf-8", "replace")))
            off = end + 1

    return d
