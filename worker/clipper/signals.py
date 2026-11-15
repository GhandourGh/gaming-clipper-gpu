"""Streamer-specific signals for finding moments (all local, $0):
- faces(): Apple Vision face detection on sampled frames -> a stable webcam box (much better than OpenCV's Haar cascade
  on small, dark or side-lit streamer cams).
- scan_video(): ONE hardware-decoded pass over the source that yields both scene cuts and facecam motion
  (the streamer jumping, leaning in, covering their face = a reaction), instead of decoding the video twice.
- chat_activity(): YouTube live-chat replay (from yt-dlp) -> messages/second and hype/laugh messages/second,
  shifted earlier because chat reacts a few seconds after the moment."""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import media

FACE_FPS = 4  # facecam motion sampling rate
FACE_PX = 32  # facecam patch is reduced to 32x32 grey: enough to see movement, tiny to process
CHAT_LAG_S = 5.0  # chat reacts ~3-8 s after the moment on screen
HYPE = re.compile(r"(?i)\b(lol+|lmao+|lmfao|kekw|omegalul|lul+|icant|pog+|poggers|w+|ww+|no+ ?way|clip (it|that)|wtf|omg|bro+|"
                  r"holy|insane|crazy|💀|😂|🤣|😭|🔥)\b|[💀😂🤣😭🔥]|!{2,}|\?{2,}")


def _vision_faces(img: Path) -> list[tuple[float, float, float, float]]:
    import Quartz
    import Vision
    from Foundation import NSURL
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(img)), None)
    if src is None:
        return []
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    handler.performRequests_error_([req], None)
    out = []
    for o in req.results() or []:
        if float(o.confidence()) < 0.5:
            continue
        b = o.boundingBox()
        out.append((float(b.origin.x), float(1 - b.origin.y - b.size.height), float(b.size.width), float(b.size.height)))
    return out


def _gray_frames(src: Path, duration: float, n: int = 24, width: int = 480, start: float = 0.0) -> np.ndarray:
    """n evenly spaced grey frames (n, h, width) as uint8 from [start, start+duration], decoded in one ffmpeg call."""
    info = media.probe(src)
    h = int(round(info["height"] * width / info["width"] / 2)) * 2
    fps = n / max(1.0, duration)
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src), "-vf", f"fps={fps:.5f},scale={width}:{h},format=gray",
                        "-frames:v", str(n), "-f", "rawvideo", "-"], capture_output=True, timeout=1800)
    a = np.frombuffer(p.stdout, dtype=np.uint8)
    k = len(a) // (width * h)
    return a[: k * width * h].reshape(k, h, width)


def refine_cam_box(frames: np.ndarray, face: dict, guess: dict) -> dict:
    """Find the webcam overlay's real rectangle. A webcam shows a mostly still room while the gameplay around it keeps
    changing, so per-pixel change over time is low inside the cam and high outside. Grow a rectangle from the face
    while the next row/column is still mostly 'still'. Falls back to the face-based guess when the contrast is unclear."""
    if len(frames) < 8:
        return guess
    S = frames.astype(np.float32).std(axis=0)
    S = (S[:-1:, :-1:] + S[1:, 1:]) / 2  # light smoothing
    H, W = S.shape
    x0, y0 = int(face["x"] * W), int(face["y"] * H)
    x1, y1 = int((face["x"] + face["w"]) * W), int((face["y"] + face["h"]) * H)
    fw, fh = max(2, x1 - x0), max(2, y1 - y0)
    ring = S[max(0, y0 - fh // 2): min(H, y1 + fh // 2), max(0, x0 - fw // 2): min(W, x1 + fw // 2)]
    s_in, s_out = float(np.median(ring)), float(np.median(S))
    if s_out < 1.6 * s_in + 2:  # the gameplay is not clearly busier than the cam: no reliable border
        return {**guess, "method": "guess"}
    thr = (s_in + s_out) / 2
    still = S < thr

    def frac(r0, r1, c0, c1):
        blk = still[max(0, r0):max(0, r1), max(0, c0):max(0, c1)]
        return float(blk.mean()) if blk.size else 0.0
    for _ in range(4):  # alternate horizontal and vertical growth until stable
        while x0 > 0 and frac(y0, y1, x0 - 1, x0) > 0.6:
            x0 -= 1
        while x1 < W and frac(y0, y1, x1, x1 + 1) > 0.6:
            x1 += 1
        while y0 > 0 and frac(y0 - 1, y0, x0, x1) > 0.6:
            y0 -= 1
        while y1 < H and frac(y1, y1 + 1, x0, x1) > 0.6:
            y1 += 1
    if (x1 - x0) < 1.5 * fw or (y1 - y0) < 1.5 * fh or (x1 - x0) * (y1 - y0) > 0.45 * W * H:
        return {**guess, "method": "guess"}
    return {"x": round(x0 / W, 3), "y": round(y0 / H, 3), "w": round((x1 - x0) / W, 3), "h": round((y1 - y0) / H, 3), "method": "still-region"}


def faces(src: Path, duration: float, samples: int = 24, start: float = 0.0, hint: dict | None = None) -> dict:
    """Stable face position across [start, start+duration] -> webcam box. Same output shape as analysis.find_facecam.
    Called once for the whole video and again per clip window (stream layouts change during a stream)."""
    hits = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(samples):
            t = start + (i + 0.5) * duration / samples
            f = Path(tmp) / f"f{i}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=960:-2",
                            "-q:v", "4", str(f)], check=False, timeout=60)
            if f.exists():
                hits += [(i, *b) for b in _vision_faces(f)]
    if not hits:
        return {"found": False, "hit_rate": 0.0, "detector": "vision"}
    a = np.array(hits)
    cx, cy = a[:, 1] + a[:, 3] / 2, a[:, 2] + a[:, 4] / 2
    # stable face positions (a moving in-game character is scattered; a webcam is not). Group streams show several
    # webcams: with a hint (where Gemini saw the streamer's own cam) take the stable face closest to it.
    clusters, used = [], np.zeros(len(a), bool)
    for j in np.argsort([-((np.abs(cx - cx[k]) < 0.06) & (np.abs(cy - cy[k]) < 0.08)).sum() for k in range(len(a))]):
        if used[j]:
            continue
        near = (np.abs(cx - cx[j]) < 0.06) & (np.abs(cy - cy[j]) < 0.08)
        used |= near
        clusters.append((near, len(set(a[near, 0].tolist()))))
    stable = [cl for cl in clusters if cl[1] / samples >= 0.3] or clusters[:1]
    if hint and len(stable) > 1 and all(k in hint for k in "xywh"):
        hx, hy = hint["x"] + hint["w"] / 2, hint["y"] + hint["h"] / 2
        best, best_n = min(stable, key=lambda cl: (np.median(cx[cl[0]]) - hx) ** 2 + (np.median(cy[cl[0]]) - hy) ** 2)
    else:
        best, best_n = max(stable, key=lambda cl: cl[1])
    rate = best_n / samples
    if rate < 0.3:
        return {"found": False, "hit_rate": round(rate, 2), "detector": "vision"}
    fw, fh = float(np.median(a[best, 3])), float(np.median(a[best, 4]))
    fx, fy = float(np.median(cx[best])), float(np.median(cy[best]))
    bw, bh = min(1.0, fw * 3.0), min(1.0, fh * 2.6)  # webcam frame around the face (head + shoulders)
    x0, y0 = min(max(0.0, fx - bw / 2), 1 - bw), min(max(0.0, fy - bh * 0.42), 1 - bh)
    face = {"x": round(fx - fw / 2, 3), "y": round(fy - fh / 2, 3), "w": round(fw, 3), "h": round(fh, 3)}
    guess = {"x": round(x0, 3), "y": round(y0, 3), "w": round(bw, 3), "h": round(bh, 3)}
    box = guess
    if fw <= 0.22:  # a webcam overlay, not a camera-only video: find its real borders
        try:
            box = refine_cam_box(_gray_frames(src, duration, start=start), face, guess)
        except Exception:  # noqa: BLE001 - the face-based guess is still usable
            box = guess
    return {"found": True, "hit_rate": round(rate, 2), "detector": "vision", "fullscreen_face": fw > 0.22,
            "face": face, "box": box, "box_guess": guess, "webcams_seen": len(stable)}


def _scan_chunk(src: Path, start: float, length: float, graph: str, face: bool) -> tuple[list[float], bytes]:
    """One time slice. Fast software decode: no deblocking and no B-frames (a 4 fps scan needs neither) is ~4x
    faster than full decoding on this Mac, and slices run in parallel."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-skip_loop_filter", "all", "-skip_frame", "bidir", "-i", str(src), "-an",
           "-filter_complex", "[0:v]" + graph, "-map", "[so]", "-f", "null", "-"]
    if face:
        cmd += ["-map", "[fo]", "-f", "rawvideo", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True, timeout=14400)
    if p.returncode != 0:  # a decoder that refuses the skip options: plain decode
        p = subprocess.run([c for c in cmd if c not in ("-skip_loop_filter", "all", "-skip_frame", "bidir")], capture_output=True, timeout=14400)
    err = p.stderr.decode(errors="replace")
    return [start + float(m) for m in re.findall(r"pts_time:([0-9.]+)", err)], p.stdout if face else b""


def scan_video(src: Path, duration: float, face_box: dict | None) -> dict:
    """Scene cuts (+ facecam motion per second when a webcam was found) from one decode of the video,
    split into parallel time slices for long sources."""
    from concurrent.futures import ThreadPoolExecutor
    info = media.probe(src)
    W, H = info["width"], info["height"]
    graph = "fps=4,scale=320:-2,split=2[s][f];[s]select='gt(scene,0.25)',showinfo[so]"
    if face_box:
        bx, by = int(face_box["x"] * 320) // 2 * 2, int(face_box["y"] * 320 * H / W) // 2 * 2
        bw, bh = max(8, int(face_box["w"] * 320) // 2 * 2), max(8, int(face_box["h"] * 320 * H / W) // 2 * 2)
        graph += f";[f]crop={bw}:{bh}:{bx}:{by},scale={FACE_PX}:{FACE_PX},format=gray[fo]"
    else:
        graph += ";[f]nullsink"
    k = 4 if duration > 600 else 1
    size = duration / k
    with ThreadPoolExecutor(k) as ex:
        parts = list(ex.map(lambda i: _scan_chunk(src, i * size, size + (0.5 if i < k - 1 else 5), graph, bool(face_box)), range(k)))
    cuts = sorted({round(c, 2) for part, _ in parts for c in part if c <= duration})
    motion = None
    if face_box:
        n = max(1, int(duration))
        per = np.zeros(n)
        for i, (_, raw) in enumerate(parts):
            fr = np.frombuffer(raw[: len(raw) // FACE_PX ** 2 * FACE_PX ** 2], dtype=np.uint8).reshape(-1, FACE_PX * FACE_PX).astype(np.float32)
            d = np.abs(np.diff(fr, axis=0)).mean(axis=1) if len(fr) > 1 else np.zeros(0)
            for j, v in enumerate(d):
                sec = int(i * size + (j + 1) / FACE_FPS)
                if sec < n:
                    per[sec] = max(per[sec], float(v))
        for c in cuts:
            for sec in (int(c) - 1, int(c), int(c) + 1):
                if 0 <= sec < n:
                    per[sec] = 0.0
        motion = per.round(2).tolist()
    return {"cuts": cuts, "face_motion": motion}


def parse_live_chat(path: Path) -> list[tuple[float, str]]:
    """YouTube live-chat or our Twitch JSONL timeline -> [(seconds, text)]."""
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "t" in d:  # provider-neutral Twitch row written by ingest.fetch_twitch_chat
            out.append((float(d["t"]), str(d.get("text") or "")))
            continue
        act = d.get("replayChatItemAction") or {}
        t = act.get("videoOffsetTimeMsec")
        for a in act.get("actions") or []:
            item = (a.get("addChatItemAction") or {}).get("item") or {}
            r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if not r or t is None:
                continue
            runs = (r.get("message") or {}).get("runs") or []
            text = "".join(x.get("text") or (x.get("emoji") or {}).get("shortcuts", [""])[0] for x in runs)
            out.append((int(t) / 1000, text))
    return out


def chat_activity(path: Path, duration: float, offset: float = 0.0) -> dict | None:
    """Per-second chat rate and hype rate, moved CHAT_LAG_S earlier to line up with what happened on screen.
    offset = where the downloaded part starts in the full stream (long streams are downloaded in sections)."""
    if not path.exists():
        return None
    msgs = parse_live_chat(path)
    if len(msgs) < 30:
        return None
    n = max(1, int(duration))
    rate, hype = np.zeros(n), np.zeros(n)
    for t, text in msgs:
        s = int(t - CHAT_LAG_S - offset)
        if 0 <= s < n:
            rate[s] += 1
            if HYPE.search(text):
                hype[s] += 1
    return {"messages": len(msgs), "rate": rate.tolist(), "hype": hype.tolist()}


def peaks(series, min_z: float = 2.5, gap: int = 6) -> list[int]:
    """Seconds where a per-second series spikes well above its usual level (robust z-score), at least `gap` s apart."""
    v = np.asarray(series, dtype=np.float64)
    if len(v) < 5 or not v.any():
        return []
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826 + 1e-6
    z = (v - med) / mad
    out = []
    for i in np.argsort(-z):
        if z[i] < min_z:
            break
        if all(abs(int(i) - j) >= gap for j in out):
            out.append(int(i))
    return sorted(out)


def gameplay_area(frames: np.ndarray, cam_box: dict | None, dark: int = 24) -> dict | None:
    """The part of the frame that really shows the game: drops black letterbox/pillarbox bands that stay dark in
    almost every sampled frame (e.g. a 2.39:1 trailer playing inside a 16:9 stream). Columns/rows covered by the
    webcam are ignored, so a cam sitting on top of a bar does not hide the bar. None = nothing worth cropping."""
    if frames is None or len(frames) < 4:
        return None
    n, H, W = frames.shape
    cols = np.ones(W, bool)
    rows = np.ones(H, bool)
    if cam_box:
        cols[int(cam_box["x"] * W): int((cam_box["x"] + cam_box["w"]) * W) + 1] = False
        rows[int(cam_box["y"] * H): int((cam_box["y"] + cam_box["h"]) * H) + 1] = False
    if cols.sum() < W * 0.3 or rows.sum() < H * 0.3:
        return None
    still_dark = (frames < dark).mean(axis=0) > 0.9  # dark in >= 90% of the sampled frames (a dark scene is not a bar)
    row_dark = still_dark[:, cols].mean(axis=1)  # share of such pixels per row, outside the cam (chat/alerts may sit in a bar)
    col_dark = still_dark[rows, :].mean(axis=0)
    def band(share, n_):
        """Length of a bar at the start of `share`: up to the first line that is almost never dark, if the lines
        before it are mostly dark (chat or alerts may cover part of a bar, the picture itself is ~0% dark)."""
        for i in range(n_ // 3):
            if share[i] < 0.15:
                return i if i >= 2 and share[:i].mean() > 0.5 else 0
        return 0
    y0 = band(row_dark, H)
    y1 = H - band(row_dark[::-1], H)
    x0 = band(col_dark, W)
    x1 = W - band(col_dark[::-1], W)
    if (y1 - y0) > 0.97 * H and (x1 - x0) > 0.97 * W:
        return None
    pad = 2  # stay just inside the picture so no dark edge line remains
    y0, y1 = min(H, y0 + pad) if y0 else 0, max(0, y1 - pad) if y1 < H else H
    x0, x1 = min(W, x0 + pad) if x0 else 0, max(0, x1 - pad) if x1 < W else W
    return {"x": round(x0 / W, 4), "y": round(y0 / H, 4), "w": round((x1 - x0) / W, 4), "h": round((y1 - y0) / H, 4)}


def audio_excitement(wav: Path) -> np.ndarray:
    """Per-second 'something is happening' score from the soundtrack alone: loud moments and screams/laughs
    (high-frequency energy while loud), robust z-scores, negatives dropped."""
    from .analysis import _z, audio_features
    f = audio_features(wav, hop=1.0)
    rms, hf = np.asarray(f["rms_db"]), np.asarray(f["hf_ratio"])
    return np.maximum(_z(rms), 0) + 0.8 * np.maximum(_z(hf * (rms > np.median(rms))), 0)


def _centred_window(score: np.ndarray, L: int) -> int:
    """Start of the L-second window with the most excitement, re-centred on where that excitement is concentrated
    (so a burst is not left at the very edge, with its aftermath cut off)."""
    win = np.convolve(score, np.ones(L), "valid")
    a = int(np.argmax(win))
    seg = score[a:a + L]
    if seg.sum() > 0:
        c = a + float((np.arange(len(seg)) * seg).sum() / seg.sum())
        a = int(min(max(0, c - L / 2), len(score) - L))
    return a


def best_section(chat_path: Path, total: float, length: float, audio_score=None) -> tuple[float, float, str]:
    """For a long stream: the `length`-second window where chat was most excited (hype counts double); without a
    chat replay, the window with the most audio excitement; without either, skip the intro and take what follows."""
    if total <= length:
        return 0.0, total, "whole video"
    msgs = parse_live_chat(chat_path) if chat_path.exists() else []
    if len(msgs) >= 100:
        n = int(total) + 1
        score = np.zeros(n)
        for t, text in msgs:
            s = int(t - CHAT_LAG_S)
            if 0 <= s < n:
                score[s] += 2.0 if HYPE.search(text) else 1.0
        L = int(length)
        a = _centred_window(score, L)
        return float(a), float(min(total, a + L)), f"busiest chat window ({len(msgs)} messages in the stream)"
    if audio_score is not None and len(audio_score) > length:
        L = int(length)
        sc = np.asarray(audio_score, dtype=np.float64)
        skip = int(min(300, len(sc) * 0.03))  # stream intros/outros are loud music, not moments
        sc[:skip] = 0
        sc[len(sc) - skip:] = 0
        a = _centred_window(sc, L)
        return float(a), float(min(total, a + L)), "no chat replay: loudest/most excited part of the audio"
    a = min(300.0, total * 0.05)
    return a, min(total, a + length), "no chat replay: after the intro"
