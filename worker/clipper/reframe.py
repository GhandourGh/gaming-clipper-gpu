"""Auto-reframe: a virtual camera that follows the action inside a 16:9 source cropped to 9:16.

A fixed crop (gameplay_focus_x) shows ~1/3 of the frame width, so the action often sits outside it. This module
plans a camera path the way a human operator would shoot it:
  - the camera HOLDS still while the subject stays inside a dead zone (no constant wobble),
  - when the subject leaves it, the camera glides with a speed and acceleration limit (no snapping, no overshoot),
  - on a hard cut in the footage it jumps instead of panning across the cut,
  - if the camera would barely move, no path is returned at all (a static, steady frame is the cleaner edit).

Subject = where the picture changes (motion centroid), ignoring the webcam box and the HUD bands at the top and
bottom. The path is in the same coordinates as `layout.gameplay_focus_x` (0..1 across the gameplay region), so the
renderer can use it wherever it used the static value."""
import json
import subprocess
from pathlib import Path

import numpy as np

ANALYSIS_FPS = 8
ANALYSIS_W = 192
HARD_CUT = 38.0        # mean abs grey difference between frames that counts as an edit cut
DEAD_ZONE = 0.30       # of the crop window width (each side): the subject may drift this far before the camera moves
SETTLE_ZONE = 0.12     # a started move ends when the subject is this close to the centre again
MAX_SPEED = 0.40       # crop windows per second (a full window width in ~1.8 s)
MAX_ACCEL = 0.8        # crop windows per second^2
MIN_TRAVEL = 0.03      # of the region width: below this total movement the clip keeps a static frame
PERSIST_S = 0.6        # the subject must stay outside the dead zone this long before the camera starts a move
KEY_EVERY = 0.25       # seconds between path keyframes before simplification


def _gray(video: Path, duration: float, fps: int = ANALYSIS_FPS, width: int = ANALYSIS_W) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-t", f"{duration:.3f}", "-i", str(video), "-vf",
                        f"fps={fps},scale={width}:-2:flags=area,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=900)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "json", str(video)], capture_output=True, text=True, timeout=60)
    s = json.loads(probe.stdout)["streams"][0]
    h = int(round(s["height"] * width / s["width"] / 2)) * 2
    a = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(a) // (width * h)
    return a[: n * width * h].reshape(n, h, width)


def subject_track(frames: np.ndarray, region: tuple[float, float], mask_box: dict | None = None
                  ) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Per frame: subject x (0..1 across `region`, NaN when there is no clear subject), confidence, hard-cut frame indices.
    region = (x0, x1) as fractions of the source width; mask_box = webcam rectangle (normalised) to ignore."""
    n, h, w = frames.shape
    x0, x1 = int(region[0] * w), max(int(region[0] * w) + 4, int(region[1] * w))
    f = frames.astype(np.float32)
    d = np.abs(np.diff(f, axis=0))                       # (n-1, h, w)
    cuts = [i + 1 for i in range(len(d)) if d[i].mean() > HARD_CUT]
    weight = np.ones((h, w), np.float32)
    weight[: int(h * 0.10)] = 0.15                       # HUD / score bars
    weight[int(h * 0.90):] = 0.15
    if mask_box:
        r0, r1 = int(mask_box["y"] * h), int((mask_box["y"] + mask_box["h"]) * h)
        c0, c1 = int(mask_box["x"] * w), int((mask_box["x"] + mask_box["w"]) * w)
        weight[max(0, r0 - 2):r1 + 2, max(0, c0 - 2):c1 + 2] = 0
    xs = np.full(n, np.nan, np.float32)
    conf = np.zeros(n, np.float32)
    k = np.ones(3, np.float32) / 3
    for i in range(1, n):
        if i in cuts:
            m = None                                      # the difference across a cut says nothing about movement
        else:
            m = d[i - 1] * weight
            m = np.where(m > 14, m, 0)[:, x0:x1]          # ignore compression noise
        if m is None:
            continue
        col = m.sum(axis=0)
        col = np.convolve(col, np.ones(5, np.float32) / 5, mode="same")
        total = float(col.sum())
        if total < 40:                                    # nothing moves: no opinion
            continue
        thr = np.percentile(col, 75)
        col = np.where(col > thr, col - thr, 0)
        if col.sum() <= 0:
            continue
        xs[i] = float((col * np.arange(len(col))).sum() / col.sum()) / (x1 - x0 - 1)
        conf[i] = min(1.0, total / (0.02 * 255 * h * (x1 - x0)))
    if n > 1:
        xs[0], conf[0] = xs[1], conf[1]
    return xs, conf, cuts


def _fill(xs: np.ndarray, conf: np.ndarray, prior: float) -> np.ndarray:
    """Hold the last confident value through frames with no opinion; median-filter the rest (kills single-frame jumps)."""
    out = np.empty_like(xs)
    last = None
    for i, v in enumerate(xs):
        if not np.isnan(v) and conf[i] >= 0.05:
            last = v
        out[i] = last if last is not None else np.nan
    if np.isnan(out).all():
        return np.full_like(out, prior)
    first = out[~np.isnan(out)][0]
    out[np.isnan(out)] = first
    pad = np.pad(out, 5, mode="edge")
    return np.array([np.median(pad[i:i + 11]) for i in range(len(out))], np.float32)


def plan_camera(subject: np.ndarray, cuts: list[int], window: float, fps: float = ANALYSIS_FPS) -> np.ndarray:
    """Simulate the operator. `window` = crop width as a fraction of the region width. Returns camera centre per frame."""
    dt = 1.0 / fps
    lo, hi = window / 2, 1 - window / 2
    cam = float(np.clip(np.median(subject[: max(2, int(fps))]), lo, hi))
    vel, moving, outside = 0.0, False, 0
    need = max(1, int(round(PERSIST_S * fps)))
    out = np.empty(len(subject), np.float32)
    cutset = set(cuts)
    for i, s in enumerate(subject):
        s = float(np.clip(s, lo, hi))
        if i in cutset:                                   # an edit cut in the footage: jump, never pan across it
            cam, vel, moving, outside = s, 0.0, False, 0
        err = s - cam
        outside = outside + 1 if abs(err) > DEAD_ZONE * window else 0
        if not moving and outside >= need:
            moving = True
        elif moving and abs(err) < SETTLE_ZONE * window:
            moving = False
        want = float(np.clip(2.2 * err, -MAX_SPEED * window, MAX_SPEED * window)) if moving else 0.0
        vel += float(np.clip(want - vel, -MAX_ACCEL * window * dt, MAX_ACCEL * window * dt))
        cam = float(np.clip(cam + vel * dt, lo, hi))
        out[i] = cam
    return out


def _simplify(keys: list[tuple[float, float]], eps: float = 0.004) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on (t, x): drop keyframes the straight line between neighbours already explains."""
    if len(keys) < 3:
        return keys
    (t0, v0), (t1, v1) = keys[0], keys[-1]
    best, bi = 0.0, 0
    for i in range(1, len(keys) - 1):
        t, v = keys[i]
        line = v0 + (v1 - v0) * (t - t0) / max(1e-9, t1 - t0)
        if abs(v - line) > best:
            best, bi = abs(v - line), i
    if best <= eps:
        return [keys[0], keys[-1]]
    return _simplify(keys[: bi + 1], eps)[:-1] + _simplify(keys[bi:], eps)


def focus_path(video: Path, duration: float, region: tuple[float, float] = (0.0, 1.0), mask_box: dict | None = None,
               window: float = 0.316, prior: float = 0.5) -> list[tuple[float, float]]:
    """Keyframes [(t, focus_x)] in clip time, or [] when a static frame is the better edit.
    focus_x is 0..1 across `region` (the same meaning as layout.gameplay_focus_x)."""
    frames = _gray(video, duration)
    if len(frames) < 4:
        return []
    xs, conf, cuts = subject_track(frames, region, mask_box)
    subject = _fill(xs, conf, prior)
    cam = plan_camera(subject, cuts, window)
    keys = []
    step = max(1, int(round(KEY_EVERY * ANALYSIS_FPS)))
    cutset = set(cuts)
    for i in range(0, len(cam), step):
        keys.append((round(i / ANALYSIS_FPS, 3), float(cam[i])))
    for c in cuts:                                        # exact keyframes on both sides of a jump so it stays a cut
        t = c / ANALYSIS_FPS
        keys += [(round(t - 0.001, 3), float(cam[max(0, c - 1)])), (round(t, 3), float(cam[c]))]
    keys = sorted(set(keys))
    # simplify between cuts only, so a jump is never smoothed into a pan
    bounds = sorted({0.0, *(c / ANALYSIS_FPS for c in cutset), duration})
    out: list[tuple[float, float]] = []
    for a, b in zip(bounds, bounds[1:]):
        seg = [k for k in keys if a - 1e-6 <= k[0] <= b + 1e-6] if b - a > 0 else []
        if len(seg) >= 2:
            seg = _simplify(seg)
        out += [k for k in seg if not out or k[0] > out[-1][0] or (k[0] == out[-1][0] and False)]
    if len(out) < 2:
        return []
    vals = [v for _, v in out]
    travel = sum(abs(b - a) for a, b in zip(vals, vals[1:]))
    if travel < MIN_TRAVEL and not cuts:
        return []
    out[-1] = (max(out[-1][0], round(duration, 3)), out[-1][1])
    return [(round(t, 3), round(v, 4)) for t, v in out]


def path_expr(keys: list[tuple[float, float]]) -> str:
    """ffmpeg expression of t giving focus_x: linear between keyframes (they are dense enough to read as smooth), held
    before the first and after the last."""
    f = lambda x: f"{x:.4f}".rstrip("0").rstrip(".") or "0"  # noqa: E731
    expr = f(keys[-1][1])
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        if t1 - t0 < 0.003:
            seg = f(v1)
        else:
            seg = f"{f(v0)}+({f(v1 - v0)})*(t-{f(t0)})/{f(t1 - t0)}"
        expr = f"if(lt(t,{f(t1)}),{seg},{expr})"
    return f"if(lt(t,{f(keys[0][0])}),{f(keys[0][1])},{expr})"


def metrics(keys: list[tuple[float, float]], duration: float, window: float = 0.316) -> dict:
    """Camera-move statistics for the scorecard: how much the frame moves, and how abruptly."""
    if len(keys) < 2:
        return {"moves": 0, "travel": 0.0, "max_speed": 0.0}
    sp = [abs(v1 - v0) / (t1 - t0) / window for (t0, v0), (t1, v1) in zip(keys, keys[1:]) if t1 - t0 >= 0.1]
    return {"moves": sum(1 for s in sp if s > 0.05), "travel": round(sum(abs(b[1] - a[1]) for a, b in zip(keys, keys[1:])), 3),
            "max_speed": round(max(sp or [0.0]), 3)}
