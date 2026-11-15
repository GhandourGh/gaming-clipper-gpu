"""Loading screens and menus: frozen-picture detection and the shorter silence threshold for them."""
import shutil
import subprocess

import pytest

from clipper import deadair
from clipper.frozen import frozen_intervals


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_frozen_stretch_is_found(tmp_path):
    src = tmp_path / "v.mp4"  # 3 s of moving picture, then the last frame held for 4 s (a menu / loading screen)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=3",
                    "-vf", "tpad=stop_mode=clone:stop_duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    found = frozen_intervals(src, 0, 7)
    assert len(found) == 1 and found[0][0] == pytest.approx(3.0, abs=0.8) and found[0][1] == pytest.approx(7.0, abs=0.5)
    assert frozen_intervals(src, 0, 2.5) == []  # only the moving part


def test_moving_picture_is_not_frozen_when_the_window_is_too_short():
    assert frozen_intervals("missing.mp4", 0, 0.5) == []
    assert frozen_intervals("missing.mp4", 0, 5) == []  # unreadable input never raises


def test_frozen_silent_gap_is_cut_sooner_than_a_silent_gap_with_motion():
    words = [{"s": 0.0, "e": 1.0, "w": "hi"}, {"s": 3.2, "e": 4.0, "w": "there"}]  # a 2.2 s silence
    quiet = [-60.0] * 100
    args = (0.0, 4.0, words, quiet, 0.1, [])
    assert not deadair.dead_stretches(*args, gap_min=3.0)                       # plain silence: left alone at this setting
    assert deadair.dead_stretches(*args, gap_min=3.0, frozen=[(1.0, 3.2)])      # the same gap on a frozen picture: cut
    assert not deadair.dead_stretches(*args, gap_min=3.0, frozen=[(1.0, 1.5)])  # frozen only for a moment: left alone


def test_loud_frozen_stretch_is_kept():
    words = [{"s": 0.0, "e": 1.0, "w": "hi"}, {"s": 3.2, "e": 4.0, "w": "there"}]
    loud = [-60.0] * 100
    for i in range(12, 30):
        loud[i] = -5.0  # something loud happens (a scream, an explosion) while the picture is still
    assert not deadair.dead_stretches(0.0, 4.0, words, loud, 0.1, [], gap_min=3.0, frozen=[(1.0, 3.2)])
