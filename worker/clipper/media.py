"""Thin ffmpeg/ffprobe helpers."""
import json
import subprocess


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
