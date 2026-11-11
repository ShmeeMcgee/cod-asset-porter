"""Batch conversion: works out what each file is, converts it, and writes the
GDT (or BO2 model JSON) so everything shows up in the game's mod tools."""
from __future__ import annotations

import os
import time
import traceback

from .anims import ANIM_EXTS, KEYED_EXTS, anim_bone_names, read_anim
from .data import ConversionError
from .gdt import SourceInfo, win, write_gdt
from .games import GAMES, Game
from .models import MODEL_EXTS, model_type, prepare_model, read_model, safe_name, write_model
from .sidecars import ImageIndex, find_images_txt, read_images_txt
from .sounds import SOUND_EXTS, convert_sound
from .textures import TEXTURE_EXTS, convert_texture
from .xanim import write_xanim
from .xbin import write_xanim_bin

KIND_ORDER = {"gdt": 0, "model": 1, "anim": 2, "texture": 3, "sound": 4}
LONG_EXTS = (".xmodel_export", ".xmodel_bin", ".xanim_export", ".xanim_bin")


def _ext(path):
    name = path.lower()
    for e in LONG_EXTS:
        if name.endswith(e):
            return e
    return os.path.splitext(name)[1]


def classify(path):
    ext = _ext(path)
    if ext == ".gdt":
        return "gdt"
    if ext == ".cast":
        from .cast import is_animation
        return "anim" if is_animation(path) else "model"
    if ext in MODEL_EXTS:
        return "model"
    if ext in ANIM_EXTS:
        return "anim"
    if ext in TEXTURE_EXTS:
        return "texture"
    if ext in SOUND_EXTS:
        return "sound"
    return None


def collect(paths):
    """Expand folders, keep supported files, stable order, no duplicates."""
    out, seen = [], set()

    def add(p):
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        try:
            kind = classify(p)
        except Exception:  # noqa: BLE001 - unreadable file: skip it
            kind = None
        if kind:
            seen.add(key)
            out.append(p)

    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()
                for f in sorted(files):
                    add(os.path.join(root, f))
        elif os.path.isfile(p):
            add(p)
    return out


def default_pack_name(paths):
    for p in paths:
        if os.path.isdir(p):
            return safe_name(os.path.basename(os.path.normpath(p)), "ported")
    if paths:
        return safe_name(os.path.basename(os.path.dirname(os.path.abspath(paths[0]))) or "ported", "ported")
    return "ported"


BO3_IMAGE = {  # slot -> (semantic, coreSemantic, compression)
    "color": ("diffuseMap", "sRGB3chAlpha", "compressed high color"),
    "normal": ("normalMap", "Normal", "compressed"),
    "spec": ("specularMap", "sRGB3ch", "compressed"),
    "gloss": ("glossMap", "Linear1ch", "compressed"),
    "ao": ("occlusionMap", "Linear1ch", "compressed"),
    "mask": ("revealMap", "Linear1ch", "compressed"),
}
LEGACY_MAT_KEYS = {"color": "colorMap", "normal": "normalMap", "spec": "specColorMap", "gloss": "cosinePowerMap"}
BO3_MAT_KEYS = {"color": "colorMap", "normal": "normalMap", "spec": "specColorMap", "gloss": "cosinePowerMap"}


def _guess_slot(name):
    low = name.lower()
    for suffix, slot in (("_n", "normal"), ("_nml", "normal"), ("_normal", "normal"),
                         ("_s", "spec"), ("_spec", "spec"), ("_g", "gloss"), ("_gloss", "gloss"),
                         ("_ao", "ao"), ("_c", "color"), ("_col", "color"), ("_d", "color")):
        if low.endswith(suffix):
            return slot
    return None


class _Run:
    def __init__(self, game: Game, out_root, pack, emit):
        self.game = game
        self.root = out_root
        self.pack = pack
        self.emit = emit
        self.textures = {}        # src path (normcase) -> [out name, slot, src]
        self.texture_names = set()
        self.gdt_materials = {}   # material name -> {slot: texture out name}
        self.gdt_models = []      # (name, type)
        self.gdt_anims = []       # (name, rig name or "", kind, looping)
        self.rigs = {}            # model output name -> Model (skeleton only)

    # -- paths ---------------------------------------------------------------
    def folder(self, kind):
        g = self.game
        if kind == "texture" and g.key == "bo2":
            d = os.path.join(self.root, g.texture_dir)   # OAT wants images/<name>.dds
            os.makedirs(d, exist_ok=True)
            return d
        base = {"model": g.model_dir, "anim": g.anim_dir,
                "texture": g.texture_dir, "sound": g.sound_dir}[kind]
        d = os.path.join(self.root, *base.split("/"), self.pack)
        os.makedirs(d, exist_ok=True)
        return d

    def rel(self, kind, filename):
        g = self.game
        base = {"model": g.model_dir, "anim": g.anim_dir,
                "texture": g.texture_dir, "sound": g.sound_dir}[kind]
        return f"{base}/{self.pack}/{filename}"

    # -- textures ------------------------------------------------------------
    def want_texture(self, src, slot):
        key = os.path.normcase(os.path.abspath(src))
        entry = self.textures.get(key)
        if entry:
            if slot and not entry[1]:
                entry[1] = slot
            return entry[0]
        base = safe_name(os.path.basename(src), "texture")
        name, n = base, 2
        while name in self.texture_names:
            name, n = f"{base}_{n}", n + 1
        self.texture_names.add(name)
        self.textures[key] = [name, slot, src]
        return name


def convert_batch(paths, game_key, out_root, rig_path=None, pack=None,
                  log=None, progress=None, should_stop=None, scale=1.0):
    """Convert everything in `paths` for `game_key`. Returns a summary dict."""
    game = GAMES[game_key]
    lines = []

    def emit(level, msg):
        lines.append(f"[{level}] {msg}" if level != "file" else f"\n== {msg}")
        if log:
            log(level, msg)

    files = collect(paths)
    kinds = {p: classify(p) for p in files}
    files.sort(key=lambda p: KIND_ORDER[kinds[p]])
    pack = safe_name(pack or default_pack_name(paths), "ported")
    summary = {"ok": 0, "failed": 0, "outputs": [], "out_root": out_root, "pack": pack}
    work = [p for p in files if kinds[p] != "gdt"]
    if not work:
        emit("warn", "No supported files found.")
        return summary

    emit("info", f"Converting {len(work)} file(s) for {game.name} into pack '{pack}'.")
    if game.notes:
        emit("warn", game.notes)
    run = _Run(game, out_root, pack, emit)
    start = time.time()
    done = 0
    total = len(work)

    def step():
        nonlocal done
        done += 1
        if progress:
            progress(done, total)

    def guarded(label, fn):
        emit("file", label)
        try:
            out = fn()
            summary["ok"] += 1
            if out:
                summary["outputs"].append(out)
                emit("ok", "-> " + os.path.relpath(out, out_root))
        except ConversionError as e:
            summary["failed"] += 1
            emit("error", str(e))
        except Exception as e:  # unexpected: keep going, details go to the log file
            summary["failed"] += 1
            emit("error", f"Unexpected error: {e}")
            lines.append(traceback.format_exc())
        step()

    # 1. GDTs that came with the inputs: material textures, anim types, rigs.
    info = SourceInfo()
    for p in (p for p in files if kinds[p] == "gdt"):
        try:
            info.add(p)
            emit("info", f"Read {os.path.basename(p)} for material and animation details.")
        except Exception as e:  # noqa: BLE001
            emit("warn", f"Couldn't read {os.path.basename(p)}: {e}")

    images = ImageIndex()
    for p in files:
        if kinds[p] == "texture":
            images.add_file(p)

    # 2. Models.
    model_names = set()
    for src in (p for p in files if kinds[p] == "model"):
        if should_stop and should_stop():
            break

        def do_model(src=src):
            model = prepare_model(read_model(src), game, emit, scale)
            base = safe_name(os.path.basename(src))
            name, n = base, 2
            while name in model_names:
                name, n = f"{base}_{n}", n + 1
            model_names.add(name)
            model.name = name
            _resolve_materials(model, src, info, images, run, emit)
            out = os.path.join(run.folder("model"), name + game.model_ext)
            write_model(model, out, game)
            mtype = model_type(model, game)
            run.gdt_models.append((name, mtype))
            if game.key == "bo2":
                from .gltf import write_oat_xmodel_json
                jdir = os.path.join(out_root, "xmodel")
                os.makedirs(jdir, exist_ok=True)
                write_oat_xmodel_json(os.path.join(jdir, name + ".json"), name,
                                      run.rel("model", name + game.model_ext), mtype)
            emit("info", f"{len(model.bones)} bones, {len(model.verts):,} verts, "
                         f"{len(model.faces):,} tris, {mtype}, materials: "
                         + ", ".join(m.name for m in model.materials))
            model.verts, model.faces = [], []      # keep only the skeleton
            run.rigs[name] = model
            run.rigs.setdefault("@src:" + safe_name(os.path.basename(src)), model)
            return out
        guarded(os.path.basename(src), do_model)

    # 3. Animations.
    fixed_rig = None
    if rig_path:
        try:
            fixed_rig = prepare_model(read_model(rig_path), game, lambda *a: None, scale)
            fixed_rig.name = safe_name(os.path.basename(rig_path))
        except Exception as e:  # noqa: BLE001
            emit("warn", f"Couldn't read skeleton {os.path.basename(rig_path)}: {e}")

    anim_names = set()
    anim_files = [p for p in files if kinds[p] == "anim"]
    if anim_files and not game.anim_format:
        emit("warn", f"Skipped {len(anim_files)} animation(s): {game.short} can't take "
                     "converted animations yet (OpenAssetTools only loads compiled ones).")
        summary["skipped"] = len(anim_files)
        for _ in anim_files:
            step()
        anim_files = []
    for src in anim_files:
        if should_stop and should_stop():
            break

        def do_anim(src=src):
            stem = safe_name(os.path.basename(src))
            hint = info.anims.get(stem, {})
            rig = _pick_rig(src, hint, fixed_rig, run, emit)
            anim = read_anim(src, rig, emit)
            if scale != 1.0:
                anim.frames = [[(tuple(c * scale for c in off), X, Y, Z) for off, X, Y, Z in fr]
                               for fr in anim.frames]
            kind = hint.get("type") or anim.kind or "relative"
            if kind not in ("relative", "absolute", "additive", "delta"):
                kind = "relative"
            looping = hint.get("looping", anim.looping)
            name, n = stem, 2
            while name in anim_names:
                name, n = f"{stem}_{n}", n + 1
            anim_names.add(name)
            rig_name = rig.name if rig is not None else _rig_for_parts(anim.parts, run)
            out = os.path.join(run.folder("anim"), name + game.anim_ext)
            if game.anim_format == "xanim_bin":
                write_xanim_bin(anim, out)
            else:
                write_xanim(anim, out)
            run.gdt_anims.append((name, rig_name or "", kind, looping))
            if not rig_name:
                emit("warn", "Couldn't tell which model this animation belongs to; "
                             "set its model in the GDT entry.")
            return out
        guarded(os.path.basename(src), do_anim)

    # 4. Textures: the ones materials use, then any other images in the list.
    for p in files:
        if kinds[p] == "texture":
            run.want_texture(p, None)
    for key, (name, slot, src) in list(run.textures.items()):
        if should_stop and should_stop():
            break

        def do_tex(name=name, slot=slot, src=src):
            out = os.path.join(run.folder("texture"), name + game.texture_ext)
            convert_texture(src, out, game, emit, slot or _guess_slot(name))
            return out
        in_batch = any(os.path.normcase(os.path.abspath(p)) == key for p in files)
        if not in_batch:
            total += 1
        guarded(os.path.basename(src), do_tex)

    # 5. Sounds.
    sound_names = set()
    for src in (p for p in files if kinds[p] == "sound"):
        if should_stop and should_stop():
            break

        def do_sound(src=src):
            base = safe_name(os.path.basename(src), "sound")
            name, n = base, 2
            while name in sound_names:
                name, n = f"{base}_{n}", n + 1
            sound_names.add(name)
            out = os.path.join(run.folder("sound"), name + ".wav")
            convert_sound(src, out, game, emit)
            return out
        guarded(os.path.basename(src), do_sound)

    # 6. GDT for the asset tools.
    if game.gdt and (run.gdt_models or run.gdt_anims or run.textures):
        try:
            gdt_path = _write_gdt(run)
            summary["gdt"] = gdt_path
            emit("ok", "GDT -> " + os.path.relpath(gdt_path, out_root))
        except Exception as e:  # noqa: BLE001
            emit("error", f"Couldn't write the GDT: {e}")

    if should_stop and should_stop():
        emit("warn", "Stopped.")
    emit("done", f"{summary['ok']} converted, {summary['failed']} failed "
                 f"in {time.time() - start:.1f}s.")
    try:
        os.makedirs(out_root, exist_ok=True)
        with open(os.path.join(out_root, f"port_log_{pack}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return summary


def _resolve_materials(model, src, info, images, run, emit):
    missing = []
    for mat in model.materials:
        maps = dict(mat.maps)
        for slot, ref in info.material_maps(mat.name).items():
            maps.setdefault(slot, ref)
        sidecar = find_images_txt(src, mat.name)
        if sidecar:
            for slot, ref in read_images_txt(sidecar).items():
                maps.setdefault(slot, ref)
        for slot, ref in maps.items():
            if slot not in ("color", "normal", "spec", "gloss", "ao"):
                continue
            path = images.find(ref, near=src)
            if path:
                mat.files[slot] = path
                mat.outputs[slot] = run.want_texture(path, slot) + run.game.texture_ext
            elif slot == "color":
                missing.append(f"{mat.name} ({os.path.basename(ref.replace(chr(92), '/'))})")
        run.gdt_materials.setdefault(mat.name, {s: os.path.splitext(o)[0] for s, o in mat.outputs.items()})
    if missing:
        emit("warn", "Couldn't find the color texture for: " + ", ".join(missing[:5])
             + (" ..." if len(missing) > 5 else "")
             + ". Add the images to the list (or keep them next to the model).")


def _pick_rig(src, hint, fixed_rig, run, emit):
    if fixed_rig is not None:
        return fixed_rig
    want = hint.get("model")
    if want:
        rig = run.rigs.get(safe_name(want)) or run.rigs.get("@src:" + safe_name(want))
        if rig is not None:
            return rig
    if os.path.splitext(src)[1].lower() not in KEYED_EXTS:
        return None
    names = {n.lower() for n in anim_bone_names(src)}
    best, best_hits = None, 0
    for key, rig in run.rigs.items():
        if key.startswith("@src:"):
            continue
        hits = len(names & {b.name.lower() for b in rig.bones})
        if hits > best_hits:
            best, best_hits = rig, hits
    if best is not None:
        emit("info", f"Using skeleton from {best.name} ({best_hits}/{len(names)} bones match).")
    return best


def _rig_for_parts(parts, run):
    names = {p.lower() for p in parts}
    best, best_score = "", 0.0
    for key, rig in run.rigs.items():
        if key.startswith("@src:"):
            continue
        bones = {b.name.lower() for b in rig.bones}
        if not bones:
            continue
        score = len(names & bones) / max(len(names | bones), 1)
        if score > best_score:
            best, best_score = rig.name, score
    return best if best_score >= 0.6 else ""


def _write_gdt(run):
    g = run.game
    entries = []
    if g.gdt == "bo3":
        image_names = {}
        for name, slot, _src in sorted(run.textures.values()):
            asset = name if name.startswith("i_") else "i_" + name
            image_names[name] = asset
            sem, core, comp = BO3_IMAGE.get(slot or _guess_slot(name) or "color")
            entries.append((asset, "image.gdf", [
                ("baseImage", win(run.rel("texture", name + g.texture_ext))),
                ("semantic", sem), ("coreSemantic", core), ("compressionMethod", comp),
                ("imageType", "Texture"), ("type", "image"), ("mipBase", "1/1"),
                ("mipMode", "Average"), ("streamable", "1"), ("glossVarianceScale", "1"),
                ("textureAtlasRowCount", "1"), ("textureAtlasColumnCount", "1")]))
        for mname, outs in sorted(run.gdt_materials.items()):
            kv = [("materialCategory", "Geometry"), ("materialType", "lit"),
                  ("template", "material.template"), ("usage", "<not in editor>"),
                  ("surfaceType", "<none>")]
            for slot, key in BO3_MAT_KEYS.items():
                if slot in outs:
                    kv.append((key, image_names.get(outs[slot], "i_" + outs[slot])))
            entries.append((mname, "material.gdf", kv))
        for name, mtype in run.gdt_models:
            entries.append((name, "xmodel.gdf", [
                ("filename", win(f"{run.pack}/{name}{g.model_ext}")),
                ("type", mtype), ("scale", "1"), ("BulletCollisionLOD", "None")]))
        for name, rig, kind, looping in run.gdt_anims:
            entries.append((name, "xanim.gdf", [
                ("filename", win(f"{run.pack}/{name}{g.anim_ext}")),
                ("model", win(f"{run.pack}/{rig}{g.model_ext}") if rig else ""),
                ("type", kind), ("looping", "1" if looping else "0"),
                ("angleError", "0.05"), ("translationError", "0.025")]))
    else:
        for mname, outs in sorted(run.gdt_materials.items()):
            kv = [("materialType", "model phong"), ("template", "material.template"),
                  ("surfaceType", "<none>"), ("usage", "<not in editor>")]
            for slot, key in LEGACY_MAT_KEYS.items():
                if slot in outs:
                    kv.append((key, win(run.rel("texture", outs[slot] + g.texture_ext))))
            entries.append((mname, "material.gdf", kv))
        for name, mtype in run.gdt_models:
            entries.append((name, "xmodel.gdf", [
                ("filename", win(f"{run.pack}/{name}{g.model_ext}")), ("type", mtype)]))
        for name, rig, kind, looping in run.gdt_anims:
            entries.append((name, "xanim.gdf", [
                ("filename", win(f"{run.pack}/{name}{g.anim_ext}")),
                ("model", win(f"{run.pack}/{rig}{g.model_ext}") if rig else ""),
                ("type", kind), ("looping", "1" if looping else "0"), ("useBones", "0"),
                ("angleError", "0.05"), ("translationError", "0.025")]))
    path = os.path.join(run.root, "source_data", run.pack + ".gdt")
    write_gdt(path, entries)
    return path
