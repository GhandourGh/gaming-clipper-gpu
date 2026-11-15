"""Extra moment signals for pass 1, all cheap and local.

  * loudness jump: how far a second rises above the seconds just before it (a scream right after normal talk). The
    existing loudness feature only sees absolute level, so a streamer who is always loud never stands out; this does.
  * ad slate: Twitch's "commercial in progress" screen (a flat blue-violet gradient). Those seconds must never become a
    clip, and they otherwise look like calm, quiet footage that the picker skips anyway but that wastes candidates when
    the audio is loud (the ad's own sound).
  * replay heatmap: YouTube's "most replayed" curve from the downloaded info file, shifted by the downloaded section.
"""
import json
import subprocess
from pathlib import Path

import numpy as np

SILENCE = -70.0


def jump_per_second(lufs, lookback: int = 8, gap: int = 2, full_scale: float = 12.0) -> np.ndarray:
    """0-1 per second: rise (in dB/LU) over the median of the `lookback` seconds that end `gap` seconds earlier.
    The baseline never drops below normal speaking level, so speech starting after silence is not a "jump"."""
    x = np.asarray(lufs, dtype=float)
    out = np.zeros(len(x))
    if not len(x):
        return out
    voiced = x[x > SILENCE + 1]
    floor = (np.median(voiced) - 3.0) if len(voiced) else SILENCE
    for t in range(len(x)):
        a, b = max(0, t - lookback), max(0, t - gap + 1)
        if b - a >= 2:
            out[t] = max(0.0, x[t] - max(float(np.median(x[a:b])), floor))
    return np.clip(out / full_scale, 0, 1)


def ad_mask(frames: np.ndarray, n: int, min_run: int = 5) -> np.ndarray:
    """frames: (T, h, w, 3) one per second. True where the picture is the flat blue-violet ad slate, for runs of at least
    `min_run` seconds (a 1-2 second flicker inside a break does not split it)."""
    out = np.zeros(n, bool)
    for t, x in enumerate(frames[:n]):
        r, g, b = x[..., 0].mean(), x[..., 1].mean(), x[..., 2].mean()
        gray = x.mean(2)
        edge = np.abs(np.diff(gray, axis=1)).mean() + np.abs(np.diff(gray, axis=0)).mean()
        out[t] = b > 1.8 * r and b > 1.6 * g and edge < 13
    for t in range(1, n - 2):
        if not out[t] and out[t - 1] and (out[t + 1] or out[t + 2]):
            out[t] = True
    run = 0
    for t in range(n + 1):
        if t < n and out[t]:
            run += 1
            continue
        if 0 < run < min_run:
            out[t - run:t] = False
        run = 0
    return out


def ad_seconds(src: Path, n: int, min_run: int = 5, timeout: int = 1800) -> np.ndarray:
    w, h = 32, 18
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-skip_frame", "nokey", "-i", str(src), "-vf", f"fps=1,scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=timeout).stdout
    size = w * h * 3
    frames = np.frombuffer(raw, np.uint8)[: len(raw) // size * size].reshape(-1, h, w, 3).astype(float)
    return ad_mask(frames, n, min_run)


def heatmap_per_second(info: dict, n: int, offset: float = 0.0) -> np.ndarray:
    """yt-dlp's `heatmap` list ({start_time, end_time, value}) -> 0-1 per second of the local file. `offset` is the start of
    the downloaded section inside the full video."""
    out = np.zeros(n)
    for h in info.get("heatmap") or []:
        try:
            a = int(float(h["start_time"]) - offset)
            b = max(a + 1, int(float(h["end_time"]) - offset))
            v = float(h.get("value", 0))
        except (KeyError, TypeError, ValueError):
            continue
        a = max(0, a)
        if a < n:
            out[a:min(b, n)] = np.maximum(out[a:min(b, n)], v)
    top = out.max()
    return out / top if top > 0 else out


def load_info(job_dir: Path) -> dict:
    for p in sorted(job_dir.glob("source*.info.json")):
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            continue
    return {}


def ad_free_ranges(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) second ranges that are NOT ads (for logging / tests)."""
    out, start = [], None
    for t, bad in enumerate(mask):
        if not bad and start is None:
            start = t
        if bad and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out
