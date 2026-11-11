"""Reader and writer for the text .xanim_export format (VERSION 3).

Every part (bone) is stored in model space for every frame, so these files
convert between games without needing the model's skeleton.
"""
from __future__ import annotations

import os
import re

from .data import Anim, ConversionError

_TOKEN = re.compile(r'"([^"]*)"|([^\s,]+)')


def _lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.split("//", 1)[0].strip()
            if line:
                yield [q if q or not u else u for q, u in _TOKEN.findall(line)]


def read_xanim(path) -> Anim:
    anim = Anim(os.path.splitext(os.path.basename(path))[0])
    section = None
    frame = part = None
    frame_numbers = []
    seen_version = False
    ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    try:
        for tok in _lines(path):
            key = tok[0].upper()
            if key == "VERSION":
                seen_version = True
            elif key == "NUMPARTS":
                section = "parts"
            elif key == "FRAMERATE":
                anim.framerate = float(tok[1])
            elif key == "NUMFRAMES":
                section = "frames"
            elif key in ("NOTETRACKS", "NUMKEYS") and section != "notes":
                section = "notes"
            elif section == "parts" and key == "PART" and len(tok) >= 3:
                anim.parts.append(tok[2])
            elif section == "frames":
                if key == "FRAME":
                    frame = [[(0.0, 0.0, 0.0), *ident] for _ in anim.parts]
                    anim.frames.append(frame)
                    frame_numbers.append(int(float(tok[1])))
                elif key == "PART":
                    part = frame[int(tok[1])]
                elif key == "OFFSET":
                    part[0] = tuple(float(t) for t in tok[1:4])
                elif key in ("X", "Y", "Z"):
                    part["XYZ".index(key) + 1] = tuple(float(t) for t in tok[1:4])
            elif section == "notes" and key == "FRAME" and len(tok) >= 3:
                anim.notes.append((int(float(tok[1])), tok[2]))
    except (IndexError, ValueError, TypeError) as e:
        raise ConversionError(f"xanim_export file looks damaged ({e}).")

    if not seen_version or not anim.parts:
        raise ConversionError("Not a valid xanim_export file.")

    # Renumber frames from 0 and shift notetracks to match.
    first = min(frame_numbers) if frame_numbers else 0
    order = sorted(range(len(anim.frames)), key=lambda i: frame_numbers[i])
    anim.frames = [[tuple(p) for p in anim.frames[i]] for i in order]
    anim.notes = [(max(0, f - first), n) for f, n in anim.notes]
    anim.first_frame = first
    return anim


def _num(v):
    return str(int(v)) if float(v).is_integer() else ("%f" % v)


def write_xanim(anim: Anim, path):
    out = []
    w = out.append
    w("// Converted by CoD Asset Porter\n\n")
    w("ANIMATION\nVERSION 3\n\n")
    w("NUMPARTS %d\n" % len(anim.parts))
    for i, name in enumerate(anim.parts):
        w('PART %d "%s"\n' % (i, name))
    w("\n")
    w("FRAMERATE %s\n" % _num(anim.framerate))
    w("NUMFRAMES %d\n" % len(anim.frames))
    for fi, frame in enumerate(anim.frames):
        w("FRAME %d\n" % fi)
        for pi, (off, X, Y, Z) in enumerate(frame):
            w("PART %d\nOFFSET %f %f %f\nSCALE 1.000000 1.000000 1.000000\n"
              "X %f %f %f\nY %f %f %f\nZ %f %f %f\n\n" %
              (pi, *off, *X, *Y, *Z))

    # Notetracks in the "NOTETRACKS" layout, which every target game reads.
    last = max(0, len(anim.frames) - 1)
    notes = sorted((min(max(0, int(f)), last), n.replace('"', "'"))
                   for f, n in anim.notes)
    w("NOTETRACKS\n\n")
    for pi in range(len(anim.parts)):
        w("PART %d\n" % pi)
        if pi == 0 and notes:
            w("NUMTRACKS 1\n\nNOTETRACK 0\nNUMKEYS %d\n" % len(notes))
            for f, n in notes:
                w('FRAME %d "%s"\n' % (f, n))
            w("\n")
        else:
            w("NUMTRACKS 0\n\n")

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(out))
