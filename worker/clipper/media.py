"""Thin ffmpeg/ffprobe helpers, plus the one place that decides how video is encoded/decoded on this host.
Linux GPU worker: NVDEC decode (-hwaccel cuda, frames handed to CPU filters) + h264_nvenc encode.
macOS: VideoToolbox for finished clips, libx264 for tiny analysis proxies."""
import json
import os
import platform
import subprocess

LINUX = platform.system() == "Linux"
NVENC = LINUX and os.environ.get("NVENC", "1") == "1"
HWDEC = LINUX and os.environ.get("HWDEC", "1") == "1"
_CQ = {"proxy": "34", "review": "30", "bg": "32", "master": "18"}


def h264(kind: str = "master") -> list[str]:
    """Video encoder arguments. kind: proxy (analysis/LLM input), review, bg (blurred background), master (clip quality)."""
    if NVENC:
        preset = "p5" if kind == "master" else "p1"
        return ["-c:v", "h264_nvenc", "-preset", preset, "-rc", "vbr", "-cq", _CQ[kind], "-b:v", "0", "-pix_fmt", "yuv420p"]
    if kind == "master" and platform.system() == "Darwin":
        return ["-c:v", "h264_videotoolbox", "-b:v", "14M", "-pix_fmt", "yuv420p"]
    crf = {"proxy": "33", "review": "30", "bg": "30", "master": "16"}[kind]
    return ["-c:v", "libx264", "-preset", "veryfast" if kind != "master" else "fast", "-crf", crf, "-pix_fmt", "yuv420p"]


def hwdec() -> list[str]:
    """Input option for GPU decoding (put before each -i). Unsupported codecs fall back to software decode."""
    return ["-hwaccel", "cuda"] if HWDEC else []


def run(cmd, timeout=3600):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-800:]}")
    return p


def probe(path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 120)
    d = json.loads(p.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    fps = 0.0
    if v and v.get("avg_frame_rate", "0/0") not in ("0/0", ""):
        n, m = v["avg_frame_rate"].split("/")
        fps = float(n) / float(m) if float(m) else 0.0
    return {
        "duration": float(d["format"].get("duration", 0)),
        "size": int(d["format"].get("size", 0)),
        "width": int(v["width"]) if v else 0, "height": int(v["height"]) if v else 0, "fps": round(fps, 3),
        "vcodec": v.get("codec_name") if v else None, "acodec": a.get("codec_name") if a else None,
        "v_duration": float(v.get("duration", 0) or 0) if v else 0.0,
        "a_duration": float(a.get("duration", 0) or 0) if a else 0.0,
        "has_video": v is not None, "has_audio": a is not None,
    }
