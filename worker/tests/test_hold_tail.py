"""The end hold: a clip whose source had no quiet after the last word gets its last frame held plus silence."""
import subprocess
from pathlib import Path

from clipper import media, render
from clipper.cutcheck import output_checks


def _clip(p: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=3:r=30",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(p)], check=True)


def test_hold_tail_extends_video_and_audio(tmp_path):
    base = tmp_path / "base.mp4"
    _clip(base)
    before = media.probe(base)["duration"]
    render._hold_tail(base, 0.45)
    after = media.probe(base)["duration"]
    assert 0.40 <= after - before <= 0.50
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0", str(base)],
                             capture_output=True, text=True).stdout
    assert "video" in streams and "audio" in streams


def test_end_room_fails_without_hold_and_passes_with_it():
    words = [{"w": "on.", "s": 48.9, "e": 49.28}]
    tight = {"captions": {"enabled": True, "words": words}}
    assert "end_room" in output_checks(tight, 49.333)["fails"]
    assert "end_room" not in output_checks(tight, 49.333 + 0.45)["fails"]
