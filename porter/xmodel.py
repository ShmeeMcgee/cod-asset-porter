"""Reader and writer for the text .xmodel_export format used by the mod tools.

Version 6 is read by the CoD4, World at War and Black Ops 1 tools,
version 7 by the Black Ops 3 tools.
"""
from __future__ import annotations

import os
import re

from .data import Bone, ConversionError, Face, Material, Model
from .mathutil import axes_to_q, q_to_axes
from .xbin import parse_image_string

_TOKEN = re.compile(r'"([^"]*)"|([^\s,]+)')


def _lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.split("//", 1)[0].strip()
            if line:
                yield [q if q or not u else u for q, u in _TOKEN.findall(line)]


def _floats(tok, n):
    return tuple(float(t) for t in tok[1:1 + n])


def read_xmodel(path) -> Model:
    name = os.path.splitext(os.path.basename(path))[0]
    model = Model(name)
    section = None
    bone = vert = face = corner = None
    bone_axes = {}
    version = None
    face_corners = []

    def finish_corner():
        if corner is not None:
            face_corners.append(tuple(corner))

    try:
        for tok in _lines(path):
            key = tok[0].upper()
            if key == "VERSION":
                version = int(tok[1])
                if version not in (6, 7):
                    raise ConversionError(
                        f"xmodel_export version {version} isn't supported "
                        "(only 6 and 7 are).")
            elif key == "NUMBONES":
                section = "bones"
            elif key == "NUMCOSMETICS":
                pass
            elif key in ("NUMVERTS", "NUMVERTS32"):
                section = "verts"
            elif key == "NUMFACES":
                section = "faces"
            elif key == "NUMOBJECTS":
                if face is not None:
                    finish_corner()
                    face.corners = face_corners
                section = "objects"
            elif key == "NUMMATERIALS":
                section = "materials"
            elif section == "bones":
                if key == "BONE" and len(tok) >= 4:
                    model.bones.append(Bone(tok[3], int(tok[2])))
                elif key == "BONE":
                    bone = int(tok[1])
                    bone_axes[bone] = {}
                elif key in ("OFFSET", "X", "Y", "Z") and bone is not None:
                    bone_axes[bone][key] = _floats(tok, 3)
            elif section == "verts":
                if key in ("VERT", "VERT32"):
                    vert = [(0.0, 0.0, 0.0), []]
                    model.verts.append(vert)
                elif key == "OFFSET":
                    vert[0] = _floats(tok, 3)
                elif key == "BONE":
                    vert[1].append((int(tok[1]), float(tok[2])))
            elif section == "faces":
                if key in ("TRI", "TRI16"):
                    if face is not None:
                        finish_corner()
                        face.corners = face_corners
                    face_corners = []
                    corner = None
                    face = Face(int(tok[1]), int(tok[2]), [])
                    model.faces.append(face)
                elif key in ("VERT", "VERT32"):
                    finish_corner()
                    corner = [int(tok[1]), (0.0, 0.0, 1.0),
                              (1.0, 1.0, 1.0, 1.0), (0.0, 0.0)]
                elif key == "NORMAL":
                    corner[1] = _floats(tok, 3)
                elif key == "COLOR":
                    corner[2] = _floats(tok, 4)
                elif key == "UV":
                    corner[3] = (float(tok[2]), float(tok[3]))
            elif section == "objects" and key == "OBJECT":
                model.objects.append(tok[2])
            elif section == "materials" and key == "MATERIAL":
                mat = Material(tok[2])
                mat.maps.update(parse_image_string(tok[4] if len(tok) > 4 else ""))
                model.materials.append(mat)
    except (IndexError, ValueError, TypeError) as e:
        raise ConversionError(f"xmodel_export file looks damaged ({e}).")

    if version is None:
        raise ConversionError("Not a valid xmodel_export file (no VERSION).")

    model.verts = [(v[0], v[1]) for v in model.verts]
    for i, b in enumerate(model.bones):
        ax = bone_axes.get(i, {})
        b.world_pos = ax.get("OFFSET", (0.0, 0.0, 0.0))
        if all(k in ax for k in "XYZ"):
            b.world_rot = axes_to_q(ax["X"], ax["Y"], ax["Z"])
        else:
            b.world_rot = (0.0, 0.0, 0.0, 1.0)
    model.faces = [f for f in model.faces if len(f.corners) == 3]
    if not model.objects:
        model.objects = [name]
    model.fill_transforms()
    return model


def write_xmodel(model: Model, path, version: int):
    out = []
    w = out.append
    w("// Converted by CoD Asset Porter\n\n")
    w("MODEL\nVERSION %d\n\n" % version)

    w("NUMBONES %d\n" % len(model.bones))
    for i, b in enumerate(model.bones):
        w('BONE %d %d "%s"\n' % (i, b.parent, b.name))
    w("\n")
    for i, b in enumerate(model.bones):
        X, Y, Z = q_to_axes(b.world_rot)
        w("BONE %d\n" % i)
        w("OFFSET %f %f %f\n" % tuple(b.world_pos))
        w("SCALE 1.000000 1.000000 1.000000\n")
        w("X %f %f %f\n" % X)
        w("Y %f %f %f\n" % Y)
        w("Z %f %f %f\n\n" % Z)

    big = version >= 7 and len(model.verts) > 0xFFFF
    vtok = "VERT32" if big else "VERT"
    w("NUMVERTS%s %d\n" % ("32" if big else "", len(model.verts)))
    for i, (pos, weights) in enumerate(model.verts):
        w("%s %d\nOFFSET %f %f %f\nBONES %d\n" %
          (vtok, i, pos[0], pos[1], pos[2], len(weights)))
        for bi, wt in weights:
            w("BONE %d %f\n" % (bi, wt))
        w("\n")

    w("NUMFACES %d\n" % len(model.faces))
    for f in model.faces:
        tri = "TRI16" if version >= 7 and (f.obj > 255 or f.mat > 255) else "TRI"
        w("%s %d %d 0 0\n" % (tri, f.obj, f.mat))
        for vi, n, c, uv in f.corners:
            w("%s %d\nNORMAL %f %f %f\nCOLOR %f %f %f %f\nUV 1 %f %f\n\n" %
              (vtok, vi, n[0], n[1], n[2], c[0], c[1], c[2], c[3], uv[0], uv[1]))

    w("NUMOBJECTS %d\n" % len(model.objects))
    for i, name in enumerate(model.objects):
        w('OBJECT %d "%s"\n' % (i, name))
    w("\n")

    w("NUMMATERIALS %d\n" % len(model.materials))
    for i, m in enumerate(model.materials):
        w('MATERIAL %d "%s" "Lambert" "%s"\n' % (i, m.name, m.color_map))
        w("COLOR 0.000000 0.000000 0.000000 1.000000\n"
          "TRANSPARENCY 0.000000 0.000000 0.000000 1.000000\n"
          "AMBIENTCOLOR 0.000000 0.000000 0.000000 1.000000\n"
          "INCANDESCENCE 0.000000 0.000000 0.000000 1.000000\n"
          "COEFFS 0.800000 0.000000\n"
          "GLOW 0.000000 0\n"
          "REFRACTIVE 6 1.000000\n"
          "SPECULARCOLOR -1.000000 -1.000000 -1.000000 1.000000\n"
          "REFLECTIVECOLOR -1.000000 -1.000000 -1.000000 1.000000\n"
          "REFLECTIVE -1 -1.000000\n"
          "BLINN -1.000000 -1.000000\n"
          "PHONG -1.000000\n\n")

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(out))
