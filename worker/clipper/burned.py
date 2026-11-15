"""Burned-in caption detection: OCR sampled frames with Apple's Vision framework (on-device, no download).
Captions = text that shows up often in one horizontal band AND keeps changing (a HUD, logo or watermark stays the same)."""
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

BAND = 0.1  # group text boxes into 10%-tall horizontal bands (0 = top of the frame)


def ocr(path: Path) -> list[dict]:
    """Text boxes in an image: text, confidence and box (x, y, w, h normalised, y from the TOP)."""
    import Quartz
    import Vision
    from Foundation import NSURL
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    if src is None:
        return []
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok = handler.performRequests_error_([req], None)
    if not (ok[0] if isinstance(ok, tuple) else ok):
        return []
    out = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        bb = obs.boundingBox()
        x, y, w, h = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
        out.append({"text": str(cand[0].string()), "conf": float(cand[0].confidence()), "x": x, "y": 1 - y - h, "w": w, "h": h})
    return out


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


CHAT = re.compile(r"^\W{0,3}[\w.]{2,25}\s*:\s")  # "username: message" (Twitch / YouTube chat overlays)


def is_caption_box(b: dict) -> bool:
    """Subtitle-like: centred, in the middle or lower part of the frame, real words, not a chat line or HUD number."""
    letters = len(re.sub(r"[^A-Za-z]", "", b["text"]))
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    return (b["conf"] >= 0.4 and letters >= 3 and abs(cx - 0.5) <= 0.2 and 0.45 <= cy <= 0.95
            and b["w"] >= 0.08 and b["h"] >= 0.03 and not CHAT.search(b["text"]))  # subtitles are big; web/UI text is small


def analyse(frames: list[list[dict]]) -> dict:
    """Decide from per-frame OCR boxes whether the video has burned-in captions, and where."""
    n = len(frames)
    seen: dict[int, list[str]] = defaultdict(list)
    for boxes in frames:
        per_band: dict[int, str] = {}
        for b in boxes:
            if not is_caption_box(b):
                continue
            band = min(9, int((b["y"] + b["h"] / 2) / BAND))
            per_band[band] = per_band.get(band, "") + " " + b["text"]
        for band, text in per_band.items():
            seen[band].append(_norm(text))
    best = None
    for band, texts in seen.items():
        coverage = len(texts) / max(1, n)
        distinct = len(set(texts)) / max(1, len(texts))
        score = coverage * distinct
        if best is None or score > best["score"]:
            best = {"band": band, "coverage": round(coverage, 2), "distinct": round(distinct, 2), "score": score}
    hits = len(seen[best["band"]]) if best else 0
    present = bool(best and best["coverage"] >= 0.1 and hits >= 4 and best["distinct"] >= 0.6)  # captions only show while people talk
    res = {"present": present, "samples": n}
    if best:
        y0 = best["band"] * BAND
        res.update(coverage=best["coverage"], distinct=best["distinct"], band=[round(y0, 2), round(y0 + BAND, 2)],
                   where="bottom" if y0 >= 0.6 else "top" if y0 < 0.3 else "middle")
    return res


def detect(src: Path, duration: float, samples: int = 40) -> dict:
    """Sample frames evenly (skipping intro/outro), OCR them and analyse. ~5-15 s on an M1."""
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i in range(samples):  # seek to each sample (decoding the whole video took ~10 min for 23 min of 1080p)
            t = duration * (0.03 + 0.94 * i / max(1, samples - 1))
            p = Path(tmp) / f"f{i:03d}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=960:-2", str(p)],
                           check=False, timeout=60)
            if p.exists():
                frames.append(ocr(p))
    res = analyse(frames)
    lo, hi = res.get("band", [0, 0])
    res["examples"] = sorted({b["text"] for fr in frames for b in fr if is_caption_box(b) and lo <= b["y"] + b["h"] / 2 < hi})[:8] if res["present"] else []
    return res
