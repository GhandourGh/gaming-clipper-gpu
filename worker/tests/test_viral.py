"""Offline tests for hook rules, moment signals, Twitch clip signal, clip floor and the live recorder helpers."""
import json
import time
from datetime import datetime, timezone

import numpy as np
import pytest

from clipper import config, gemini, hookrules, live, momentum, twitchclips
from clipper.edl import EDL, check
from tests.test_core import base_edl


# ---- hook rules
@pytest.mark.parametrize("hook", [
    "What happens if you shoot the moon?", "4 new features coming", "Kids can't play this", "He's 72 years old",
    "Me watching this with my mom in the room", "Why are the hostages singing?", "The craziest escape ever"])
def test_good_hooks_pass(hook):
    assert hookrules.check_hook(hook)[0] == []


@pytest.mark.parametrize("hook,needle", [
    ("Wait for it...", "vague"), ("GTA 6 graphics", "bare topic label"),
    ("Full reaction compilation", "labels the clip"), ("Hi", "words"),
    ("This is a really long hook that goes on and on forever", "words"),
    ("He drove the car down the road today", "plain statement")])
def test_bad_hooks_fail(hook, needle):
    fails, _ = hookrules.check_hook(hook)
    assert fails and any(needle in f for f in fails), fails


def test_title_rules():
    assert hookrules.check_title("THIS IS ALL CAPS")[0]
    assert hookrules.check_title("He did WHAT in the HELL NOW")[0]
    assert not hookrules.check_title("Why he quit the game")[0]
    topic = hookrules.topic_pattern("GTA 6")
    assert hookrules.check_title("Funny cop chase", topic=topic)[0]
    assert not hookrules.check_title("Funny cop chase", hashtags=["#gta6"], topic=topic)[0]
    assert topic.search("GTA VI trailer") and topic.search("Grand Theft Auto 6")


def test_playbook_and_prompt_text():
    assert hookrules.kind_for("stream", None) == "reaction"
    assert hookrules.kind_for("cinematic", "story") == "showcase"
    assert hookrules.kind_for("gameplay", "funny", has_voice=False) == "gameplay"
    assert "Prefer these hook structures" in hookrules.playbook_text("stream")
    assert "2-8 words" in hookrules.prompt_rules()
    assert "{" not in hookrules.prompt_rules() and "{" not in hookrules.playbook_text("stream")  # safe for str.format


def test_edl_hook_errors_only_when_strict():
    e = base_edl(hook={"text": "Wait for it"})
    strict_errors, _, _ = check(e, 9, 31, strict_hook=True)
    lax_errors, lax_warn, _ = check(e, 9, 31, strict_hook=False)
    assert any("hook" in x for x in strict_errors)
    assert not any("hook" in x for x in lax_errors) and any("hook" in x for x in lax_warn)


# ---- signals
def test_jump_detects_scream_after_talk_but_not_constant_loudness():
    talk = [-24.0] * 60
    talk[30] = -8.0
    j = momentum.jump_per_second(talk)
    assert j[30] > 0.9 and j[:25].max() == 0
    loud = [-10.0] * 60
    assert momentum.jump_per_second(loud).max() == 0
    # speech starting after silence is not a jump
    quiet = [-70.0] * 30 + [-24.0] * 30
    assert momentum.jump_per_second(quiet)[30:].max() < 0.3


def test_ad_mask_requires_a_run_and_ignores_normal_footage():
    rng = np.random.default_rng(0)
    game = rng.integers(0, 255, (40, 18, 32, 3)).astype(float)
    slate = np.zeros((40, 18, 32, 3))
    slate[..., 0], slate[..., 1], slate[..., 2] = 40, 40, 200
    frames = game.copy()
    frames[10:25] = slate[10:25]   # 15 s ad
    frames[30:32] = slate[30:32]   # 2 s blip: too short
    m = momentum.ad_mask(frames, 40)
    assert m[10:25].all() and not m[:10].any() and not m[25:].any()


def test_heatmap_offset_and_normalisation():
    info = {"heatmap": [{"start_time": 100, "end_time": 110, "value": 0.5}, {"start_time": 110, "end_time": 120, "value": 1.0}]}
    h = momentum.heatmap_per_second(info, 30, offset=100)
    assert h[0] == 0.5 and h[10] == 1.0 and h.max() == 1.0
    assert momentum.heatmap_per_second({}, 10).sum() == 0


def test_score_timeline_uses_extra_signals_and_suppresses_ads():
    from clipper.analysis import score_timeline
    n, hop = 120, 0.5
    rng = np.random.default_rng(1)
    feats = {"hop": hop, "rms_db": (-30 + rng.normal(0, 0.5, n * 2)).tolist(), "hf_ratio": rng.random(n * 2).tolist()}
    tr = {"words": [], "segments": []}
    base = score_timeline(n, feats, [], tr)["score"]
    ads = np.zeros(n, bool)
    ads[40:60] = True
    heat = np.zeros(n)
    heat[90:100] = 1.0
    out = score_timeline(n, feats, [], tr, extra={"ads": ads, "heat": heat})
    assert out["score"][40:60].max() < base.min()
    assert out["score"][92:98].mean() > base[92:98].mean() + 1
    assert "jump" in out["components"] and "heat" in out["components"] and "ad" in out["components"]


# ---- twitch viewer clips
def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_clips_to_signal_places_bumps_before_creation():
    t0 = 1_800_000_000.0
    clips = [{"created_at": _iso(t0 + 300), "duration": 30, "view_count": 500},
             {"created_at": _iso(t0 + 305), "duration": 30, "view_count": 20}]
    sig = np.array(twitchclips.clips_to_signal(clips, t0, 600))
    assert sig.max() == 1.0 and 270 <= int(sig.argmax()) <= 302
    assert sig[:200].max() == 0 and sig[400:].max() == 0
    assert twitchclips.clips_to_signal([], t0, 10) == [0.0] * 10
    assert twitchclips.clips_to_signal([{"bad": 1}], t0, 10) == [0.0] * 10


# ---- clip floor
def test_clip_floor_scales_with_length():
    assert gemini.clip_floor(30) == 0
    assert gemini.clip_floor(100) == 1
    assert gemini.clip_floor(400) == min(config.MIN_CLIPS, 4)
    assert gemini.clip_floor(3600) == config.MIN_CLIPS


def test_top_up_adds_near_misses_only(monkeypatch):
    ranks = [{"idx": i, "start": i * 100.0, "end": i * 100.0 + 40, "quality": q, "confidence": 0.5, "is_highlight": q >= 6}
             for i, q in enumerate([7.5, 5.0, 4.2, 2.0])]
    monkeypatch.setattr(gemini.db, "q1", lambda *a, **k: {"meta": {"source": {"duration": 1800}}})
    monkeypatch.setattr(gemini.db, "event", lambda *a, **k: None)

    def iou(a, b):
        return 0.0
    out = gemini._top_up_to_floor("j", [ranks[0]], ranks, iou, explicit=False)
    assert [r["idx"] for r in out] == [0, 1, 2]           # floor of 3; 5.0 and 4.2 are within 2 points of 6.0; 2.0 is not
    assert out[1]["below_quality_bar"] and "below_quality_bar" not in out[0]
    # an explicit clip count is left to the existing top-up logic
    assert gemini._top_up_to_floor("j", [ranks[0]], ranks, iou, explicit=True) == [ranks[0]]


# ---- live recorder helpers
def test_parse_ref_accepts_platforms_only():
    assert live.parse_ref("twitch:xqc") == ("twitch", "xqc", "https://www.twitch.tv/xqc")
    assert live.parse_ref("youtube:@KaiCenat")[2] == "https://www.youtube.com/@KaiCenat/live"
    assert live.parse_ref("https://kick.com/xqc")[0] == "kick"
    for bad in ("https://evil.example/xqc", "http://localhost:8787/x", "twitch:a/../b", "file:///etc/passwd", "foo"):
        with pytest.raises(ValueError):
            live.parse_ref(bad)


def test_topic_filter_and_chunk_name():
    s = {"topic": "gta 6, gta vi"}
    assert live.topic_ok(s, {"title": "GTA 6 trailer reaction"})
    assert not live.topic_ok(s, {"title": "Minecraft with friends", "tags": ["minecraft"]})
    assert live.topic_ok({"topic": ""}, {"title": "anything"})
    p = live.Path("/x/seg-20261115-143005.ts")
    assert live.chunk_start(p) == datetime(2026, 11, 15, 14, 30, 5).timestamp()
    assert live.chunk_start(live.Path("/x/other.ts")) is None


def test_ffmpeg_cmd_segments_and_maps_split_streams(tmp_path):
    one = live.ffmpeg_cmd(["https://a/m3u8"], tmp_path, 600)
    assert one.count("-i") == 1 and "-map" not in one and "600" in one and one[-1].endswith("seg-%Y%m%d-%H%M%S.ts")
    two = live.ffmpeg_cmd(["https://a/v", "https://a/a"], tmp_path)
    assert two.count("-i") == 2 and "-map" in two


def test_add_stream_requires_rights_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "STREAMS_FILE", tmp_path / "s.json")
    with pytest.raises(ValueError):
        live.add_stream("twitch:xqc", rights_confirmed=False)
    s = live.add_stream("twitch:xqc", True, topic="gta 6", clips=99)
    assert s["clips"] == 8 and s["name"] == "xqc-twitch"
    assert live.update_stream("xqc-twitch", enabled=False)["enabled"] is False
    assert live.remove_stream("xqc-twitch") and live.load_streams() == []
    with pytest.raises(KeyError):
        live.update_stream("nope", enabled=True)


def test_finished_chunks_skip_the_one_being_written(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path)
    d = tmp_path / "s"
    d.mkdir()
    old = time.time() - 100
    for n in ("seg-20261115-100000.ts", "seg-20261115-103000.ts"):
        (d / n).write_bytes(b"x")
        import os
        os.utime(d / n, (old, old))
    assert [p.name for p in live.finished_chunks("s", recording=True)] == ["seg-20261115-100000.ts"]
    assert len(live.finished_chunks("s", recording=False)) == 2


# ---- eval set (regression for moment picking)
def test_eval_cases_pass():
    from clipper import evalset
    cases = evalset.load_cases()
    assert len(cases) >= 3
    for r in map(evalset.run_case, cases):
        assert r["ok"], r


def test_eval_ad_case_fails_without_the_ad_mask():
    """Negative control: the ad case must be able to fail, otherwise it proves nothing."""
    from clipper import evalset
    from clipper.analysis import pick_candidates, score_timeline
    case = next(c for c in evalset.load_cases() if c["name"] == "ad-break-is-not-a-moment")
    feats, tr, _ = evalset.synth(case)
    n = int(case["duration"])
    cands = pick_candidates(n, score_timeline(n, feats, [], tr, extra={}), tr, 8)
    assert any(200 <= c["peak_time"] < 300 for c in cands)
