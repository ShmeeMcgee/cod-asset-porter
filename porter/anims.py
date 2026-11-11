"""Animation reading: every supported format -> a baked `Anim`.

.seanim and .cast store keyframes per bone relative to the parent bone, often
for only some bones. xanim_export/xanim_bin need every bone in model space on
every frame, so keyed formats are baked against a skeleton (the model the
animation belongs to). xanim_export/xanim_bin are already baked.
"""
from __future__ import annotations

import bisect
import os

from .data import Anim, ConversionError, Model
from .mathutil import q_mul, q_normalize, q_rotate, q_slerp, q_to_axes, v_add, v_lerp
from .seanim import (TYPE_ABSOLUTE, TYPE_ADDITIVE, TYPE_DELTA, TYPE_NAMES,
                     read_seanim)
from .sidecars import read_nt_export
from .xanim import read_xanim
from .xbin import read_xanim_bin

ANIM_EXTS = {".seanim", ".xanim_export", ".xanim_bin"}   # + .cast (sniffed)
KEYED_EXTS = {".seanim", ".cast"}


def _ext(path):
    low = path.lower()
    for e in (".xanim_export", ".xanim_bin"):
        if low.endswith(e):
            return e
    return os.path.splitext(low)[1]


def read_keyed(path):
    if _ext(path) == ".cast":
        from .cast import read_cast_anim
        return read_cast_anim(path)
    return read_seanim(path)


def anim_bone_names(path):
    """Bone names an animation drives (used to find a matching skeleton)."""
    if _ext(path) in KEYED_EXTS:
        return read_keyed(path).bone_names
    return []


def _sample(keys, frame, lerp):
    if not keys:
        return None
    frames = [k[0] for k in keys]
    i = bisect.bisect_right(frames, frame)
    if i == 0:
        return keys[0][1]
    if i >= len(keys):
        return keys[-1][1]
    f0, v0 = keys[i - 1]
    f1, v1 = keys[i]
    if f1 == f0:
        return v1
    return lerp(v0, v1, (frame - f0) / (f1 - f0))


def _lerp_partial(a, b, t):
    return tuple(None if x is None or y is None else x + (y - x) * t for x, y in zip(a, b))


def bake_keyed(path, rig: Model, log) -> Anim:
    d = read_keyed(path)
    anim = Anim(os.path.splitext(os.path.basename(path))[0])
    anim.framerate = d.framerate
    anim.notes = list(d.notes)
    anim.looping = d.looped
    anim.kind = TYPE_NAMES.get(d.anim_type, "relative")

    rig.fill_transforms()
    rig.reorder_bones_parents_first()
    by_name = {b.name.lower(): i for i, b in enumerate(rig.bones)}

    tracks, own_types = {}, {}
    missing = []
    for ai, name in enumerate(d.bone_names):
        ri = by_name.get(name.lower())
        if ri is None:
            if d.tracks[ai]:
                missing.append(name)
        else:
            tracks[ri] = d.tracks[ai]
            if ai in d.bone_types:
                own_types[ri] = d.bone_types[ai]
    if not tracks and d.bone_names:
        raise ConversionError(
            f"None of this animation's bones exist in the skeleton '{rig.name}'. "
            "Pick the model this animation was made for.")
    if missing:
        log("warn", f"{len(missing)} animated bone(s) aren't in '{rig.name}' and "
                    f"were skipped: {', '.join(missing[:6])}"
                    + ("..." if len(missing) > 6 else ""))

    # Anim type per bone: modifiers override a bone and all its children,
    # per-bone types (Cast curves) apply to that bone only.
    overrides = {}
    for ai, t in d.modifiers.items():
        if 0 <= ai < len(d.bone_names):
            ri = by_name.get(d.bone_names[ai].lower())
            if ri is not None:
                overrides[ri] = t
    inherited = []
    for i, b in enumerate(rig.bones):
        if i in overrides:
            inherited.append(overrides[i])
        elif b.parent >= 0:
            inherited.append(inherited[b.parent])
        else:
            inherited.append(d.anim_type)
    types = [own_types.get(i, inherited[i]) if i not in overrides else overrides[i]
             for i in range(len(rig.bones))]

    if TYPE_ADDITIVE in set(types) and d.anim_type == TYPE_ADDITIVE:
        log("warn", "Additive animation: baked on top of the rest pose. "
                    "It may look different from in-game, where it's layered.")
    if d.anim_type == TYPE_DELTA:
        log("info", "Delta animation: root motion kept on the first bone.")

    for f in range(d.frame_count):
        world = [None] * len(rig.bones)
        frame = []
        for i, b in enumerate(rig.bones):
            lpos, lrot = b.local_pos, b.local_rot
            tr = tracks.get(i)
            if tr:
                kpos = _sample(tr.get("loc"), f, _lerp_partial)
                krot = _sample(tr.get("rot"), f, q_slerp)
                absolute = types[i] in (TYPE_ABSOLUTE, TYPE_DELTA)
                if kpos is not None:
                    base = b.local_pos if absolute else (0.0, 0.0, 0.0)
                    kpos = tuple(base[k] if kpos[k] is None else kpos[k] for k in range(3))
                    lpos = kpos if absolute else v_add(b.local_pos, kpos)
                if krot is not None:
                    lrot = krot if absolute else q_normalize(q_mul(b.local_rot, krot))
            if b.parent >= 0:
                ppos, prot = world[b.parent]
                wrot = q_normalize(q_mul(prot, lrot))
                wpos = v_add(ppos, q_rotate(prot, lpos))
            else:
                wpos, wrot = lpos, lrot
            world[i] = (wpos, wrot)
            frame.append((wpos, *q_to_axes(wrot)))
        anim.frames.append(frame)

    anim.parts = [b.name for b in rig.bones]
    log("info", f"{anim.kind} anim, {d.frame_count} frames @ {d.framerate:g} fps, "
                f"{len(tracks)} animated bone{'s' if len(tracks) != 1 else ''}, "
                f"{len(anim.notes)} notetrack{'s' if len(anim.notes) != 1 else ''}")
    return anim


def read_anim(src, rig: Model | None, log) -> Anim:
    ext = _ext(src)
    if ext in (".xanim_export", ".xanim_bin"):
        anim = read_xanim(src) if ext == ".xanim_export" else read_xanim_bin(src)
        nt = read_nt_export(src)
        if nt is not None:
            notes, first = nt
            if first is None:
                first = getattr(anim, "first_frame", 0)
            have = set(anim.notes)
            added = [(max(0, f - first), n) for f, n in notes if (max(0, f - first), n) not in have]
            anim.notes.extend(added)
            if added:
                log("info", f"Added {len(added)} notetrack{'s' if len(added) != 1 else ''} "
                            "from the .NT_EXPORT file.")
        log("info", f"{len(anim.frames)} frames @ {anim.framerate:g} fps, "
                    f"{len(anim.parts)} bones, {len(anim.notes)} notetracks")
    elif ext in KEYED_EXTS:
        if rig is None:
            raise ConversionError(
                f"This {ext} needs its model's skeleton. Add the model it belongs "
                "to into the list, or pick it under 'Skeleton for animations'.")
        anim = bake_keyed(src, rig, log)
    else:
        raise ConversionError(f"Unsupported animation format '{ext}'.")
    if not anim.frames:
        raise ConversionError("Animation has no frames.")
    return anim
