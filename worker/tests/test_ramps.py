"""Slow-motion ramps: splitting, timing maps, limits and the real ffmpeg cut."""
import json
import shutil
import subprocess

import pytest

from clipper.edl import EDL, apply_ramps, check, kept_duration, to_output_time


def edl(**kw):
    d = {"title": "Test clip title", "description": "A test clip", "hook": {"text": "He never saw it coming..."},
         "segments": [{"start": 0, "end": 40, "reason": "the whole scene"}], "layout": {"mode": "full"}, "style": "viral"}
    d.update(kw)
    return EDL.model_validate(d)


def ramp(a, b, speed=0.5):
    return {"start": a, "end": b, "speed": speed, "reason": "the hit lands in slow motion"}


def test_ramp_splits_segment_and_keeps_total_consistent():
    e, notes = apply_ramps(edl(ramps=[ramp(10, 12)]), 1)
    assert not notes and e.ramps == []
    assert [(s.start, s.end, s.speed) for s in e.segments] == [(0, 10, 1.0), (10, 12, 0.5), (12, 40, 1.0)]
    assert kept_duration(e) == pytest.approx(42.0)  # the 2 s ramp plays for 4 s


def test_output_time_after_a_ramp_shifts_by_the_extra_time():
    e, _ = apply_ramps(edl(ramps=[ramp(10, 12)]), 1)
    assert to_output_time(e.segments, 9) == pytest.approx(9)
    assert to_output_time(e.segments, 11) == pytest.approx(12)   # 10 + (11-10)/0.5
    assert to_output_time(e.segments, 20) == pytest.approx(22)   # 2 s later than without the ramp


def test_bad_ramps_are_dropped_and_the_limit_is_enforced():
    e, notes = apply_ramps(edl(ramps=[ramp(10, 10.4), ramp(0, 2)]), 1)  # too short; too close to the segment edge
    assert len(e.segments) == 1 and len(notes) == 2
    e, notes = apply_ramps(edl(ramps=[ramp(5, 7), ramp(20, 22)]), 1)
    assert sum(1 for s in e.segments if s.speed < 1) == 1 and any("limit" in n for n in notes)
    e, _ = apply_ramps(edl(ramps=[ramp(5, 7)]), 0)
    assert len(e.segments) == 1


def test_check_counts_slowed_time_and_respects_style_limits():
    errors, warnings, out = check(edl(ramps=[ramp(10, 12)]), 0, 40, (40, 50))
    assert not errors and kept_duration(out) == pytest.approx(42.0)
    errors, warnings, out = check(edl(style="minimal", ramps=[ramp(10, 12)]), 0, 40, (30, 50))
    assert len(out.segments) == 1 and any("slow-motion" in w for w in warnings)  # minimal allows none
    errors, warnings, out = check(edl(ramps=[ramp(10, 12)]), 0, 40, (30, 50), job_meta={"mode": "continuous"})
    assert len(out.segments) == 1


def test_check_is_idempotent():
    _, _, once = check(edl(ramps=[ramp(10, 12)]), 0, 40, (30, 50))
    _, _, twice = check(once, 0, 40, (30, 50))
    assert [(s.start, s.end, s.speed) for s in once.segments] == [(s.start, s.end, s.speed) for s in twice.segments]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_real_cut_has_the_slowed_length_and_in_sync_audio(tmp_path):
    from clipper import render
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(src)], check=True)
    e, _ = apply_ramps(edl(segments=[{"start": 0, "end": 10, "reason": "the whole scene"}], ramps=[ramp(4, 6)]), 1)
    out = tmp_path / "base.mp4"
    render._base(src, e, out, {"width": 320, "height": 180})
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(out)],
                                      capture_output=True, text=True, check=True).stdout)
    d = {s["codec_type"]: float(s["duration"]) for s in probe["streams"]}
    assert d["video"] == pytest.approx(12.0, abs=0.3) and d["audio"] == pytest.approx(12.0, abs=0.3)
