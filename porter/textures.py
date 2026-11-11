"""Texture conversion to the image format and sizes each game's tools want."""
from __future__ import annotations

import math
import struct

from .data import ConversionError
from .games import Game

TEXTURE_EXTS = {".png", ".dds", ".tga", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp"}


def _to_8bit(im):
    """High bit-depth images (16-bit PNG/TIFF, float) -> 8-bit."""
    import numpy as np
    from PIL import Image

    if im.mode.startswith("I;16"):
        arr = np.asarray(im, dtype=np.uint16)
        return Image.fromarray((arr >> 8).astype(np.uint8), "L")
    if im.mode == "I":
        arr = np.asarray(im, dtype=np.int64)
        if arr.max() > 255:
            arr = arr >> 8
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L")
    if im.mode == "F":
        arr = np.asarray(im, dtype=np.float32)
        if arr.max() <= 1.0:
            arr = arr * 255.0
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L")
    return im


def _pow2(n, cap):
    p = 2 ** max(0, round(math.log2(max(1, n))))
    return min(p, cap) if cap else p


def fix_normal_map(im, log):
    """Rebuild the blue (Z) channel of two-channel normal maps.

    Games often store only X/Y: either in red/green with blue empty (BC5),
    or X in alpha and Y in green (DXT5nm). The mod tools want a full RGB map.
    """
    import numpy as np
    from PIL import Image

    rgba = np.asarray(im.convert("RGBA"), dtype=np.float32)
    r, g, b, a = (rgba[..., i] for i in range(4))
    if a.std() > 8 and r.std() < 4 and b.std() < 4:
        x, y, how = a, g, "alpha/green"
    elif b.std() < 4 and b.mean() < 40 and (r.std() > 4 or g.std() > 4):
        x, y, how = r, g, "red/green"
    else:
        return im
    nx = x / 127.5 - 1.0
    ny = y / 127.5 - 1.0
    nz = np.sqrt(np.clip(1.0 - nx * nx - ny * ny, 0.0, 1.0))
    out = np.stack([(nx + 1) * 127.5, (ny + 1) * 127.5, (nz + 1) * 127.5], axis=-1)
    log("info", f"Rebuilt a 2-channel normal map ({how}) into full RGB.")
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def write_dds_rgba(im, path):
    """Uncompressed 32-bit DDS (OpenAssetTools reads these)."""
    im = im.convert("RGBA")
    w, h = im.size
    r, g, b, a = im.split()
    from PIL import Image
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()   # stored as BGRA
    DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8
    header = struct.pack("<4sIIIIIII44x", b"DDS ", 124, DDSD, h, w, w * 4, 0, 1)
    pf = struct.pack("<IIIIIIII", 32, 0x41 | 0x0, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    caps = struct.pack("<IIII4x", 0x1000, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(header + pf + caps + data)


def convert_texture(src, out_path, game: Game, log, slot=None) -> str:
    try:
        from PIL import Image
    except ImportError:
        raise ConversionError("Pillow isn't installed (run run.bat once to set it up).")

    try:
        with Image.open(src) as im:
            im.load()
            im = _to_8bit(im)
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"Couldn't read this image ({e}). If it's a DDS in "
                              "an unusual format, export it as PNG instead.")

    name = src.lower().rsplit(".", 1)[0]
    if slot == "normal" or (slot is None and name.endswith(("_n", "_nml", "_normal"))):
        im = fix_normal_map(im, log)

    has_alpha = im.mode in ("RGBA", "LA", "PA") or \
        (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    if has_alpha and im.getchannel("A").getextrema() == (255, 255):
        im = im.convert("RGB")   # alpha that's fully opaque is just wasted space

    w, h = im.size
    nw, nh = w, h
    if game.power_of_two:
        nw, nh = _pow2(w, game.max_texture), _pow2(h, game.max_texture)
    elif game.max_texture and max(w, h) > game.max_texture:
        s = game.max_texture / max(w, h)
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
    if (nw, nh) != (w, h):
        im = im.resize((nw, nh), Image.LANCZOS)
        log("info", f"Resized {w}x{h} -> {nw}x{nh} for {game.short}.")

    if game.texture_ext == ".tga":
        im.save(out_path, format="TGA", rle=False)
    elif game.texture_ext == ".dds":
        write_dds_rgba(im, out_path)
    else:
        im.save(out_path, format="TIFF")
    log("info", f"{nw}x{nh} {'RGBA' if im.mode == 'RGBA' else 'RGB'}"
                + (f" ({slot} map)" if slot else ""))
    return out_path
