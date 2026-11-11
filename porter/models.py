"""Model reading, clean-up and writing for every supported format."""
from __future__ import annotations

import os
import re

from .data import Bone, ConversionError, Material, Model
from .games import Game
from .semodel import read_semodel
from .xbin import read_xmodel_bin, write_xmodel_bin
from .xmodel import read_xmodel, write_xmodel

MODEL_EXTS = {".semodel", ".xmodel_export", ".xmodel_bin"}   # + .cast (sniffed)


def _ext(path):
    low = path.lower()
    for e in (".xmodel_export", ".xmodel_bin"):
        if low.endswith(e):
            return e
    return os.path.splitext(low)[1]


def read_model(path) -> Model:
    ext = _ext(path)
    if ext == ".semodel":
        return read_semodel(path)
    if ext == ".xmodel_export":
        return read_xmodel(path)
    if ext == ".xmodel_bin":
        return read_xmodel_bin(path)
    if ext == ".cast":
        from .cast import read_cast_model
        return read_cast_model(path)
    raise ConversionError(f"Unsupported model format '{ext}'.")


def safe_name(name, fallback="asset"):
    """Asset names the mod tools accept: lowercase letters, digits, underscores."""
    base = os.path.basename(name.replace("\\", "/"))
    low = base.lower()
    for e in (".xmodel_export", ".xmodel_bin", ".xanim_export", ".xanim_bin"):
        if low.endswith(e):
            base = base[:-len(e)]
            break
    else:
        base = os.path.splitext(base)[0]
    base = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")
    return base or fallback


def model_type(model: Model, game: Game):
    if len(model.bones) <= 1:
        return "rigid"
    low = model.name.lower()
    looks_like_hands = ("viewhand" in low or "viewarm" in low
                        or (low.startswith(("vm_", "viewmodel_")) and "arm" in low))
    if game.gdt == "legacy" and looks_like_hands:
        return "viewhands"
    return "animated"


def prepare_model(model: Model, game: Game, log, scale=1.0) -> Model:
    if not model.verts or not model.faces:
        raise ConversionError("Model has no geometry.")

    if not model.bones:
        b = Bone("tag_origin", -1)
        b.world_pos = b.local_pos = (0.0, 0.0, 0.0)
        b.world_rot = b.local_rot = (0.0, 0.0, 0.0, 1.0)
        model.bones.append(b)
    model.fill_transforms()
    model.reorder_bones_parents_first()

    if scale != 1.0:
        s = float(scale)
        for b in model.bones:
            b.world_pos = tuple(c * s for c in b.world_pos)
            b.local_pos = tuple(c * s for c in b.local_pos)
        model.verts = [(tuple(c * s for c in p), w) for p, w in model.verts]

    # Bone names must be unique for every game's tools.
    seen = {}
    for b in model.bones:
        key = b.name.lower()
        if key in seen:
            seen[key] += 1
            b.name = f"{b.name}_{seen[key]}"
        else:
            seen[key] = 0

    if game.max_bones and len(model.bones) > game.max_bones:
        log("warn", f"{len(model.bones)} bones; {game.short} may only accept "
                    f"{game.max_bones}. The asset tools will say if it's too many.")

    if game.max_verts and len(model.verts) > game.max_verts:
        raise ConversionError(
            f"{len(model.verts):,} vertices is more than {game.short} allows "
            f"({game.max_verts:,}). Export a lower LOD of this model and try again.")

    # Bone weights: drop invalid ones, keep the strongest, renormalise.
    nb = len(model.bones)
    trimmed = 0
    new_verts = []
    for pos, weights in model.verts:
        merged = {}
        for b, w in weights:
            if 0 <= b < nb and w > 0.0:
                merged[b] = merged.get(b, 0.0) + w
        ws = sorted(merged.items(), key=lambda bw: -bw[1])
        if len(ws) > game.max_influences:
            ws = ws[:game.max_influences]
            trimmed += 1
        total = sum(w for _, w in ws)
        ws = [(b, w / total) for b, w in ws] if total > 0 else [(0, 1.0)]
        new_verts.append((pos, ws))
    model.verts = new_verts
    if trimmed:
        log("info", f"{trimmed:,} vertices had more than {game.max_influences} "
                    f"bone weights; kept the strongest {game.max_influences}.")

    # Degenerate triangles make the converters complain.
    before = len(model.faces)
    model.faces = [f for f in model.faces
                   if len({c[0] for c in f.corners}) == 3
                   and all(0 <= c[0] < len(model.verts) for c in f.corners)]
    dropped = before - len(model.faces)
    if dropped:
        log("info", f"Removed {dropped:,} broken triangle{'s' if dropped > 1 else ''}.")
    if not model.faces:
        raise ConversionError("Model has no usable triangles.")

    if not model.objects:
        model.objects = [model.name]
    for f in model.faces:
        if not 0 <= f.obj < len(model.objects):
            f.obj = 0
    model.objects = [safe_name(o, f"{safe_name(model.name)}_{i}") for i, o in enumerate(model.objects)]

    # Unused materials are dropped; names made tool-safe and unique.
    if not model.materials:
        model.materials.append(Material("default"))
    for f in model.faces:
        if not 0 <= f.mat < len(model.materials):
            f.mat = 0
    used = sorted({f.mat for f in model.faces})
    remap = {old: new for new, old in enumerate(used)}
    model.materials = [model.materials[i] for i in used]
    for f in model.faces:
        f.mat = remap[f.mat]
    names = set()
    for m in model.materials:
        base = safe_name(m.name, "material")
        name, n = base, 2
        while name in names:
            name, n = f"{base}_{n}", n + 1
        names.add(name)
        m.name = name
    return model


def recenter_viewmodel(model: Model):
    """Newer games author viewmodels with tag_view at eye height (e.g. z=60).
    CoD4/WaW/BO1 expect tag_view at the origin, so move the whole model down.
    Returns the shift applied (also stored as model.shift), or None."""
    roots = [b for b in model.bones if b.parent < 0]
    if len(roots) != 1 or roots[0].name.lower() != "tag_view":
        return None
    p = roots[0].world_pos
    if max(abs(c) for c in p) < 0.5:
        return None
    shift = (-p[0], -p[1], -p[2])
    model.verts = [((v[0] + shift[0], v[1] + shift[1], v[2] + shift[2]), w) for v, w in model.verts]
    for b in model.bones:
        b.world_pos = tuple(b.world_pos[k] + shift[k] for k in range(3))
        if b.parent < 0:
            b.local_pos = b.world_pos
    model.shift = shift
    return shift


def far_from_root(model: Model, limit=4.0):
    """BO1's converter stops and asks "Continue?" when a mesh sits far from the
    model's origin compared with its own size (it calls this "rotation
    invariant bounds far exceed physical bounds"). Small viewmodel parts placed
    out in front of the camera trip it. Returns (radius, distance) when the
    model looks like one of those, else None. The radius is half the bounding
    box diagonal and the distance is the farthest vertex from the origin, the
    same numbers the converter prints."""
    pts = [p for p, _ in model.verts]
    if not pts:
        return None
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    radius = 0.5 * sum((hi[i] - lo[i]) ** 2 for i in range(3)) ** 0.5
    dist = max((p[0] ** 2 + p[1] ** 2 + p[2] ** 2) ** 0.5 for p in pts)
    if radius > 0 and dist > limit * radius:
        return radius, dist
    return None


def write_model(model: Model, path, game: Game):
    if game.model_format == "xmodel6":
        write_xmodel(model, path, 6)
    elif game.model_format == "xmodel_bin":
        write_xmodel_bin(model, path)
    elif game.model_format == "glb":
        from .gltf import write_glb
        write_glb(model, path)
    else:
        raise ConversionError(f"Unknown model format {game.model_format}.")
