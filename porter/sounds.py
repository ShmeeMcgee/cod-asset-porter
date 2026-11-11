"""Sound conversion to 16-bit PCM WAV at the sample rate each game expects."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import wave

from .data import ConversionError
from .games import Game

SOUND_EXTS = {".wav", ".flac", ".ogg", ".mp3"}


def _read_wav_bytes(buf):
    """Parse a WAV (PCM 8/16/24/32-bit or float) into float32 (frames, channels)."""
    import numpy as np

    if buf[:4] != b"RIFF" or buf[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    off, fmt, data = 12, None, None
    while off + 8 <= len(buf):
        cid, size = buf[off:off + 4], struct.unpack_from("<I", buf, off + 4)[0]
        body = off + 8
        size = min(size, len(buf) - body)
        if cid == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", buf, body)
            if tag == 0xFFFE and size >= 26:
                tag = struct.unpack_from("<H", buf, body + 24)[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            data = buf[body:body + size]
        off = body + size + (size & 1)
    if not fmt or data is None:
        raise ValueError("missing fmt/data chunk")
    tag, ch, rate, bits = fmt
    width = bits // 8
    data = data[:len(data) - len(data) % (width * ch)]
    if tag == 3 and bits in (32, 64):
        x = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif tag == 1 and bits == 8:
        x = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 1 and bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"WAV encoding {tag}/{bits}-bit")
    return x.reshape(-1, ch), rate


def _read_audio(path):
    ext = os.path.splitext(path)[1].lower()
    errors = []
    if ext == ".wav":
        try:
            with open(path, "rb") as f:
                return _read_wav_bytes(f.read())
        except Exception as e:
            errors.append(str(e))
    try:
        import soundfile
        x, rate = soundfile.read(path, dtype="float32", always_2d=True)
        return x, rate
    except ImportError:
        errors.append("soundfile not installed")
    except Exception as e:
        errors.append(str(e))
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-f", "wav",
                            "-acodec", "pcm_f32le", "-"], capture_output=True)
        if r.returncode == 0:
            return _read_wav_bytes(r.stdout)
        errors.append(r.stderr.decode(errors="replace").strip()[:200])
    raise ConversionError("Couldn't decode this sound (" + "; ".join(errors) + ").")


def _resample(x, src, dst):
    import numpy as np
    if src == dst:
        return x
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(src), int(dst))
        return resample_poly(x, dst // g, src // g, axis=0).astype(np.float32)
    except ImportError:
        # FFT resampling: band-limited, so no aliasing (scipy isn't bundled in the .exe).
        n_in = len(x)
        n_out = max(1, int(round(n_in * dst / src)))
        pad = min(n_in, 4096)                      # mirror-pad to soften edge effects
        xp = np.concatenate([x[pad - 1::-1], x, x[:-pad - 1:-1]], axis=0) if pad else x
        spec = np.fft.rfft(xp, axis=0)
        n_big = int(round(len(xp) * dst / src))
        out_bins = n_big // 2 + 1
        keep = min(out_bins, spec.shape[0])
        new = np.zeros((out_bins, x.shape[1]), dtype=spec.dtype)
        new[:keep] = spec[:keep]
        y = np.fft.irfft(new, n=n_big, axis=0) * (n_big / len(xp))
        start = int(round(pad * dst / src))
        return y[start:start + n_out].astype(np.float32)


def convert_sound(src, out_path, game: Game, log) -> str:
    try:
        import numpy as np
    except ImportError:
        raise ConversionError("numpy isn't installed (run run.bat once to set it up).")
    x, rate = _read_audio(src)
    if x.size == 0:
        raise ConversionError("Sound file is empty.")
    if x.shape[1] > 2:
        log("warn", f"{x.shape[1]} channels; kept the first two (stereo).")
        x = x[:, :2]
    x = _resample(x, int(rate), game.sound_rate)
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2")
    with wave.open(out_path, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(game.sound_rate)
        w.writeframes(pcm.tobytes())
    secs = len(pcm) / game.sound_rate
    log("info", f"{'stereo' if pcm.shape[1] == 2 else 'mono'}, {rate} Hz -> "
                f"{game.sound_rate} Hz 16-bit, {secs:.2f}s")
    return out_path
