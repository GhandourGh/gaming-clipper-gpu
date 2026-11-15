"""Editing-quality tests: restraint limits, endings that land, and the auto-reframe camera."""
import shutil
import subprocess

import numpy as np
import pytest

from clipper import reframe, timing
from clipper.edl import EDL, check


def base_edl(**kw):
    d = {"title": "Test clip title", "description": "A test clip", "hook": {"text": "He never saw it coming..."},
         "segments": [{"start": 10, "end": 30, "reason": "the whole story"}],
         "layout": {"mode": "full"}, "style": "viral"}
    d.update(kw)
    return EDL.model_validate(d)


def zoom(t, scale=1.3, dur=0.8):
    return {"start": t, "end": t + dur, "target": "gameplay", "scale": scale, "reason": "punch on the action"}


def sfx(t, gain=-8):
    return {"at": t, "sound": "punch", "gain_db": gain, "reason": "impact on the hit"}


# ---------- restraint ----------

def test_small_overshoot_is_trimmed_not_sent_back():
    e = base_edl(zooms=[zoom(11), zoom(15), zoom(19), zoom(23), zoom(27)])  # 20 s viral: limit 3, five given
    errors, warnings, out = check(e, 9, 31)
    assert not any("too many zooms" in x for x in errors)
    assert len(out.zooms) <= 3 and any("trimmed" in w for w in warnings)


def test_big_overshoot_goes_back_to_the_director():
    e = base_edl(zooms=[zoom(10 + 2 * i) for i in range(9)])
    assert any("too many zooms" in x for x in check(e, 9, 31)[0])


def test_zooms_need_a_breath_between_them_and_scale_is_capped():
    e = base_edl(zooms=[zoom(12, 1.7), zoom(13.0, 1.3)])  # second starts 0.2 s after the first ends
    _, warnings, out = check(e, 9, 31)
    assert len(out.zooms) == 1 and out.zooms[0].scale == 1.45
    assert any("too close" in w for w in warnings)


def test_stacked_sound_effects_are_dropped():
    e = base_edl(sfx=[sfx(12.0), sfx(12.3), sfx(20.0)])
    _, _, out = check(e, 9, 31)
    assert [round(s.at, 1) for s in out.sfx] == [12.0, 20.0]


def test_doctrine_is_in_every_style_guide():
    from clipper.styles import STYLES
    assert all("Editing doctrine" in s["guide"] for s in STYLES.values())


# ---------- endings ----------

WORDS = [{"w": "no", "s": 20.0, "e": 20.3}, {"w": "way.", "s": 20.4, "e": 20.9}, {"w": "Okay", "s": 24.0, "e": 24.3}]


def test_ending_keeps_a_beat_when_nobody_talks():
    out = timing.extend_tail([{"start": 10, "end": 21.0, "role": "main"}], WORDS, limit=40)
    assert 21.5 <= out[0]["end"] <= 21.7


def test_ending_never_runs_into_the_next_line():
    out = timing.extend_tail([{"start": 10, "end": 23.7, "role": "main"}], WORDS, limit=40)
    assert out[0]["end"] < 24.0 - 0.07  # stops before "Okay"


def test_ending_stays_inside_the_source_window():
    out = timing.extend_tail([{"start": 10, "end": 21.0, "role": "main"}], WORDS, limit=21.2)
    assert out[0]["end"] <= 21.2


def test_cold_open_is_never_extended():
    segs = [{"start": 30, "end": 32, "role": "cold_open"}, {"start": 10, "end": 21.0, "role": "main"}]
    assert timing.extend_tail(segs, WORDS, limit=40)[0]["end"] == 32


# ---------- auto-reframe ----------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_video(path, xs_per_frame, w=640, h=360, fps=8, cuts=()):
    """Dark frame with a bright 70 px block whose centre x is given per frame; `cuts` are frame indexes where the picture
    changes completely (a hard edit)."""
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps),
                          "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", str(path)], stdin=subprocess.PIPE)
    rng = np.random.default_rng(1)
    for i, x in enumerate(xs_per_frame):
        f = np.full((h, w), 30 + (60 if any(i >= c for c in cuts) and len([c for c in cuts if i >= c]) % 2 else 0), np.uint8)
        f[150:220, max(0, int(x) - 35):int(x) + 35] = 235
        f = np.clip(f.astype(np.int16) + rng.integers(-2, 3, f.shape), 0, 255).astype(np.uint8)
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()


@needs_ffmpeg
def test_camera_follows_a_subject_that_leaves_the_frame_window(tmp_path):
    xs = [120] * 16 + list(np.linspace(120, 520, 40)) + [520] * 24  # 10 s: still, runs right, still
    make_video(tmp_path / "a.mp4", xs)
    keys = reframe.focus_path(tmp_path / "a.mp4", 10.0, window=0.316)
    assert keys, "a subject crossing the frame must produce a camera path"
    assert keys[0][1] < 0.35 and keys[-1][1] > 0.7
    # smooth: no step bigger than the speed limit allows between keyframes
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t1 - t0 >= 0.1:
            assert abs(v1 - v0) / (t1 - t0) <= reframe.MAX_SPEED * 0.316 * 1.6


@needs_ffmpeg
def test_a_steady_scene_keeps_a_static_frame(tmp_path):
    make_video(tmp_path / "s.mp4", [320] * 80)
    assert reframe.focus_path(tmp_path / "s.mp4", 10.0, window=0.316) == []


@needs_ffmpeg
def test_small_wobble_inside_the_dead_zone_does_not_move_the_camera(tmp_path):
    xs = [320 + 12 * np.sin(i / 3) for i in range(80)]
    make_video(tmp_path / "w.mp4", xs)
    assert reframe.focus_path(tmp_path / "w.mp4", 10.0, window=0.316) == []


def test_path_expression_matches_the_keyframes():
    keys = [(0.0, 0.3), (2.0, 0.3), (3.0, 0.7), (5.0, 0.7)]
    expr = reframe.path_expr(keys)

    def _if(c, a, b):
        return a if c else b
    env = {"_if": _if, "_lt": lambda a, b: a < b}
    py = expr.replace("if(", "_if(").replace("lt(", "_lt(")
    val = lambda t: eval(py, {**env, "t": t})  # noqa: E731
    assert val(-1) == pytest.approx(0.3) and val(1.0) == pytest.approx(0.3)
    assert val(2.5) == pytest.approx(0.5, abs=1e-3) and val(4.0) == pytest.approx(0.7)


def test_hard_cut_jumps_instead_of_panning():
    subject = np.array([0.2] * 16 + [0.8] * 16, np.float32)
    cam = reframe.plan_camera(subject, [16], window=0.316)
    assert abs(cam[16] - 0.8) < 0.02 and abs(cam[15] - 0.2) < 0.02
