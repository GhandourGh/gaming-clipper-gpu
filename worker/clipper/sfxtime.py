"""Sound-effect timing: the AI's timestamps are approximate (it sees video at 2 fps), so every sound effect is moved onto
the real moment in the audio, measured in milliseconds:
  1) the action: the strongest sudden rise in loudness (an onset: a hit, a scream, a crash, a word that lands) within
     +-0.6 s of the AI's time, found on a 10 ms grid of the clip's own audio;
  2) the sound's own peak: an impact (boom, punch, thud) peaks in its first few ms, a whoosh peaks in the middle, a riser
     at its end. The sound starts at  action - peak, so its loudest instant lands exactly on the action: impacts hit
     with it, whooshes and risers build up BEFORE it and peak on it.
When there is no clear onset near the AI's time (talking over steady noise), the AI's time is kept."""
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

HOP = 0.01      # 10 ms analysis grid
SEARCH = 0.6    # look this far around the AI's time
SR = 16000


def _env(x: np.ndarray, sr: int) -> np.ndarray:
    n = int(sr * HOP)
    k = len(x) // n
    if k == 0:
        return np.zeros(0)
    fr = x[: k * n].reshape(k, n).astype(np.float64)
    return np.sqrt((fr ** 2).mean(axis=1) + 1e-12)


def _read(wav: Path, start: float, end: float) -> np.ndarray:
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        a = max(0, int(start * sr))
        w.setpos(min(a, w.getnframes()))
        raw = w.readframes(max(0, int((end - start) * sr)))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def onset_near(wav: Path, t: float) -> float | None:
    """Time of the strongest sudden loudness rise within +-SEARCH s of t, or None if nothing stands out."""
    lo = max(0.0, t - SEARCH - 0.1)
    try:
        x = _read(wav, lo, t + SEARCH + 0.1)
    except (OSError, wave.Error):
        return None
    env = _env(x, SR)
    if len(env) < 10:
        return None
    db = 20 * np.log10(env + 1e-6)
    flux = np.maximum(0, db[2:] - db[:-2])  # rise over 20 ms
    if flux.size == 0:
        return None
    i = int(np.argmax(flux))
    if flux[i] < max(6.0, float(np.median(flux)) * 4):  # no clear hit: keep the AI's time
        return None
    return round(lo + (i + 1) * HOP, 3)


@lru_cache(maxsize=256)
def peak_offset(sfx_file: str) -> float:
    """Seconds from the start of a sound effect file to its loudest instant."""
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", sfx_file, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                             capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    env = _env(np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, SR)
    return round(float(np.argmax(env)) * HOP, 3) if len(env) else 0.0


def align(wav: Path, src_t: float, sfx_file: Path, visual: list[float] | None = None) -> tuple[float, str]:
    """(source time to START the sound, note). The sound's peak lands on the action next to the AI's time.
    A sound that goes with something on screen (text popping in, a flash, a zoom punch within 0.5 s) locks to that
    visual moment instead of the audio."""
    pk = peak_offset(str(sfx_file)) if sfx_file.exists() else 0.0
    near = [v for v in (visual or []) if abs(v - src_t) <= 0.5]
    if near:
        v = min(near, key=lambda v: abs(v - src_t))
        return round(max(0.0, v - pk), 3), f"peak on the on-screen event at {v:.2f}s"
    if not wav.exists():
        return src_t, "no audio to align to"
    hit = onset_near(wav, src_t)
    if hit is None:
        return src_t, "no clear action nearby: kept the AI's time"
    return round(max(0.0, hit - pk), 3), f"peak on the action at {hit:.2f}s (sound peaks {pk * 1000:.0f} ms in)"
