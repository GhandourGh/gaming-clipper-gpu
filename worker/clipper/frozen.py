"""Frozen-picture detection: loading screens, pause menus, map screens and frozen frames show a (nearly) unchanging
picture. ffmpeg's freezedetect finds them cheaply on a tiny, low-fps copy of the video. A frozen stretch with nobody
talking and nothing loud is safe to cut, so it is cut after a shorter pause than ordinary silence (see deadair.py)."""
import re
import subprocess

FREEZE_MIN_S = 1.0     # a picture must stand still this long to count
NOISE = 0.01           # tolerance for compression noise / a small spinner (0 = identical frames)


def frozen_intervals(src, start: float, end: float, min_s: float = FREEZE_MIN_S, noise: float = NOISE) -> list[tuple[float, float]]:
    """Source-time (start, end) stretches inside [start, end] where the picture does not change."""
    dur = max(0.0, end - start)
    if dur < min_s:
        return []
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-an",
           "-vf", f"fps=5,scale=160:-2,freezedetect=n={noise}:d={min_s}", "-f", "null", "-"]
    try:
        err = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stderr
    except (OSError, subprocess.SubprocessError):
        return []
    out, open_at = [], None
    for line in err.splitlines():
        m = re.search(r"freeze_(start|end):\s*([0-9.]+)", line)
        if not m:
            continue
        t = float(m.group(2))
        if m.group(1) == "start":
            open_at = t
        elif open_at is not None:
            out.append((start + open_at, start + t))
            open_at = None
    if open_at is not None:  # frozen until the end of the window
        out.append((start + open_at, end))
    return [(round(a, 3), round(min(b, end), 3)) for a, b in out if b - a >= min_s]
