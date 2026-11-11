"""Small files exporters write next to the real assets.

* `<anim>.NT_EXPORT`     - notetracks kept outside the xanim (WaW / BO1 tools)
* `<material>_images.txt` - which image each material slot uses (Wraith/Greyhound)
* finding the image file an image name refers to
"""
from __future__ import annotations

import os
import re

_NT_FRAME = re.compile(r'^\s*FRAME\s+(-?\d+)\s+"([^"]*)"', re.I)

# exporter semantic names -> our material slots
SEMANTICS = {
    "diffuse_map": "color", "diffusemap": "color", "colormap": "color", "color": "color",
    "albedo": "color", "albedomap": "color", "basecolor": "color", "diffuse": "color",
    "normal_map": "normal", "normalmap": "normal", "normal": "normal",
    "specular_map": "spec", "specularmap": "spec", "speccolormap": "spec", "specular": "spec",
    "gloss_map": "gloss", "glossmap": "gloss", "cosinepowermap": "gloss", "gloss": "gloss",
    "occlusion_map": "ao", "occmap": "ao", "ao": "ao",
}


def _sibling(path, ext):
    """Same-stem file with another extension, any letter case."""
    folder = os.path.dirname(path) or "."
    stem = os.path.basename(path)
    low = stem.lower()
    for e in (".xanim_export", ".xanim_bin", ".seanim", ".cast"):
        if low.endswith(e):
            stem = stem[:-len(e)]
            break
    want = (stem + ext).lower()
    try:
        for name in os.listdir(folder):
            if name.lower() == want:
                return os.path.join(folder, name)
    except OSError:
        pass
    return None


def read_nt_export(anim_path):
    """-> ([(frame, name)], first_frame or None), or None if there's no file."""
    nt = _sibling(anim_path, ".nt_export")
    if not nt:
        return None
    notes, first = [], None
    with open(nt, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _NT_FRAME.match(line)
            if m:
                notes.append((int(m.group(1)), m.group(2)))
            elif line.strip().upper().startswith("FIRSTFRAME"):
                try:
                    first = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
    return notes, first


def read_images_txt(path):
    """Wraith / Greyhound '<material>_images.txt' -> {slot: image name}."""
    out = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            sem, sep, img = line.strip().partition(",")
            if not sep or sem.lower() == "semantic" or not img:
                continue
            slot = SEMANTICS.get(sem.strip().lower())
            if slot and slot not in out:
                out[slot] = img.strip()
    return out


def find_images_txt(model_path, material_name):
    """Look for '<material>_images.txt' near a model file."""
    folder = os.path.dirname(model_path) or "."
    want = (material_name + "_images.txt").lower()
    for d in (folder, os.path.join(folder, "_images"),
              os.path.join(os.path.dirname(folder), "_images")):
        try:
            for name in os.listdir(d):
                if name.lower() == want:
                    return os.path.join(d, name)
        except OSError:
            continue
    return None


IMAGE_EXTS = (".png", ".tga", ".dds", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp")


def _keys(name):
    """Name variants an image might be saved under."""
    base = os.path.basename(name.replace("\\", "/"))
    stem = os.path.splitext(base)[0] if os.path.splitext(base)[1].lower() in IMAGE_EXTS else base
    low = stem.lower()
    out = [low]
    clean = re.sub(r"[~$&]", "", low).lstrip("-")
    clean = re.sub(r"^-?[a-z]?-", "", clean) if clean.startswith(("-g", "g-")) else clean
    for v in (clean, re.sub(r"[^a-z0-9_]+", "_", low).strip("_")):
        if v and v not in out:
            out.append(v)
    if low.startswith("i_"):
        out.append(low[2:])
    return out


class ImageIndex:
    """Finds image files by name across the inputs and folders near models."""

    def __init__(self):
        self.by_key = {}
        self.scanned = set()

    def add_file(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        for k in _keys(stem):
            self.by_key.setdefault(k, path)

    def add_folder(self, folder, depth=2):
        folder = os.path.normpath(folder)
        if folder in self.scanned or not os.path.isdir(folder):
            return
        self.scanned.add(folder)
        try:
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and name.lower().endswith(IMAGE_EXTS):
                    self.add_file(full)
                elif depth > 0 and os.path.isdir(full) and \
                        name.lower() in ("_images", "images", "textures", "texture_assets"):
                    self.add_folder(full, depth - 1)
        except OSError:
            pass

    def find(self, ref, near=None):
        if not ref:
            return None
        if near:
            direct = os.path.join(os.path.dirname(near), ref.replace("\\", os.sep))
            if os.path.isfile(direct):
                return direct
            self.add_folder(os.path.dirname(near))
            self.add_folder(os.path.dirname(os.path.dirname(near)), depth=1)
        for k in _keys(ref):
            hit = self.by_key.get(k)
            if hit:
                return hit
        return None
