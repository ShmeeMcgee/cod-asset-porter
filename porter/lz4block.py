"""LZ4 block compression used by BO3's .xmodel_bin / .xanim_bin files.

Uses the fast `lz4` package when it's installed (the Windows build bundles it)
and falls back to a pure-Python codec so the tool works without it.
"""
from __future__ import annotations

try:  # pragma: no cover - depends on the environment
    import lz4.block as _fast
except Exception:  # noqa: BLE001
    _fast = None


def decompress(src: bytes, size: int) -> bytes:
    if _fast is not None:
        return _fast.decompress(src, uncompressed_size=size)
    src = bytes(src)
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if lit:
            dst += src[i:i + lit]
            i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0 or off > len(dst):
            raise ValueError("corrupt LZ4 data")
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(dst) - off
        if off >= ml:
            dst += dst[start:start + ml]
        else:  # overlapping copy repeats the last `off` bytes
            seg = bytes(dst[start:])
            dst += (seg * (ml // off + 1))[:ml]
    if len(dst) != size:
        raise ValueError(f"LZ4 size mismatch ({len(dst)} != {size})")
    return bytes(dst)


def _write_len(out, v):
    while v >= 255:
        out.append(255)
        v -= 255
    out.append(v)


def compress(src: bytes) -> bytes:
    if _fast is not None:
        return _fast.compress(src, store_size=False)
    src = bytes(src)
    n = len(src)
    out = bytearray()
    anchor = 0
    i = 0
    limit = n - 12          # the format needs the last 12 bytes free of match starts
    table = {}
    misses = 0
    while i < limit:
        key = src[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is not None and i - cand <= 0xFFFF:
            m = 4
            maxm = n - 5 - i  # matches must end 5+ bytes before the end
            while m + 32 <= maxm and src[cand + m:cand + m + 32] == src[i + m:i + m + 32]:
                m += 32
            while m < maxm and src[cand + m] == src[i + m]:
                m += 1
            lit = src[anchor:i]
            L, M = len(lit), m - 4
            out.append((min(L, 15) << 4) | min(M, 15))
            if L >= 15:
                _write_len(out, L - 15)
            out += lit
            out += (i - cand).to_bytes(2, "little")
            if M >= 15:
                _write_len(out, M - 15)
            i += m
            anchor = i
            misses = 0
            continue
        misses += 1
        i += 1 + (misses >> 6)
    lit = src[anchor:]
    L = len(lit)
    out.append(min(L, 15) << 4)
    if L >= 15:
        _write_len(out, L - 15)
    out += lit
    return bytes(out)
