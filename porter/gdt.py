"""GDT files: the asset lists Asset Manager (CoD4/WaW/BO1) and APE (BO3) use.

Reading a GDT that came with a port pack tells us which textures each
material uses, which model each animation belongs to, and anim types.
Writing one means converted assets show up in the tools ready to convert.
"""
from __future__ import annotations

import os
import re

_ENTRY = re.compile(r'"([^"]+)"\s*([\(\[])\s*"([^"]+)"\s*[\)\]]\s*\{(.*?)\n\s*\}', re.S)
_KV = re.compile(r'"([^"]*)"\s+"([^"]*)"')


def read_gdt(path):
    """-> list of (name, gdf, {key: value})."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    out = []
    for m in _ENTRY.finditer(text):
        out.append((m.group(1), m.group(3).lower(), dict(_KV.findall(m.group(4)))))
    return out


def _stem(p):
    p = p.replace("\\", "/").rsplit("/", 1)[-1]
    low = p.lower()
    for e in (".xmodel_export", ".xmodel_bin", ".xanim_export", ".xanim_bin"):
        if low.endswith(e):
            return low[:-len(e)]
    return os.path.splitext(low)[0]


class SourceInfo:
    """Facts gathered from GDTs that came with the inputs."""

    MAT_KEYS = {"colorMap": "color", "normalMap": "normal", "specColorMap": "spec",
                "cosinePowerMap": "gloss", "occMap": "ao"}

    def __init__(self):
        self.images = {}     # image asset name -> baseImage path
        self.materials = {}  # material name (lower) -> {slot: image ref}
        self.anims = {}      # file stem -> {"type", "looping", "model"}
        self.models = {}     # file stem -> {"type", "name"}

    def add(self, path):
        for name, gdf, kv in read_gdt(path):
            if gdf == "image.gdf":
                if kv.get("baseImage"):
                    self.images[name.lower()] = kv["baseImage"]
            elif gdf == "material.gdf":
                maps = {}
                for key, slot in self.MAT_KEYS.items():
                    v = kv.get(key, "")
                    if v and not v.startswith("$"):
                        maps[slot] = self.images.get(v.lower(), v)
                self.materials[name.lower()] = maps
            elif gdf == "xanim.gdf" and kv.get("filename"):
                self.anims[_stem(kv["filename"])] = {
                    "type": kv.get("type", ""), "looping": kv.get("looping", "0") == "1",
                    "model": _stem(kv.get("model", "")) if kv.get("model") else ""}
            elif gdf == "xmodel.gdf" and kv.get("filename"):
                self.models[_stem(kv["filename"])] = {"type": kv.get("type", ""), "name": name}
        # image entries may come after the materials that use them
        for maps in self.materials.values():
            for slot, ref in list(maps.items()):
                maps[slot] = self.images.get(ref.lower(), ref)

    def material_maps(self, name):
        return self.materials.get(name.lower(), {})


def write_gdt(path, entries):
    """entries: list of (name, gdf, [(key, value), ...])."""
    out = ["{\n"]
    for name, gdf, kv in entries:
        out.append('\t"%s" ( "%s" )\n\t{\n' % (name, gdf))
        for k, v in kv:
            out.append('\t\t"%s" "%s"\n' % (k, str(v).replace('"', "'")))
        out.append("\t}\n")
    out.append("}\n")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("".join(out))


def win(path):
    """GDT paths use doubled backslashes."""
    return path.replace("/", "\\").replace("\\", "\\\\")
