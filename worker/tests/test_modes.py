"""Clip modes and per-job options: defaults, limits, rules and silence cutting."""
from clipper import deadair, modes
from clipper.edl import EDL, check
from clipper.styles import STYLES


def edl(**kw):
    d = {"title": "Test clip title", "description": "A test clip", "hook": {"text": "He never saw it coming..."},
         "segments": [{"start": 10, "end": 90, "reason": "the whole scene"}], "layout": {"mode": "full"}, "style": "viral"}
    d.update(kw)
    return EDL.model_validate(d)


def zoom(t, scale=1.5):
    return {"start": t, "end": t + 0.8, "target": "gameplay", "scale": scale, "reason": "look at the action"}


def sfx(t):
    return {"at": t, "sound": "punch", "gain_db": -8, "reason": "impact on the hit"}


def test_normalize_defaults_per_mode():
    assert modes.normalize({})["mode"] == "viral"
    c = modes.normalize({"mode": "continuous"})
    assert (c["edit"], c["silence"], c["zoom"], c["sfx"]) == ("minimal", "low", "low", "off")
    assert modes.normalize({"mode": "nonsense", "zoom": "ultra"})["zoom"] == "medium"
    assert modes.normalize({"mode": "continuous", "sfx": "high"})["sfx"] == "high"  # the owner's choice wins


def test_instructions_are_cleaned_and_capped():
    o = modes.normalize({"mode": "custom", "instructions": "  keep   all\n dialogue " + "x" * 900})
    assert o["instructions"].startswith("keep all dialogue") and len(o["instructions"]) <= 600
    assert "instructions" not in modes.normalize({"mode": "custom", "instructions": "  "})


def test_style_follows_mode_and_edit():
    assert modes.style_for("viral", "dynamic") == "fast" and modes.style_for("viral", "balanced") == "viral"
    assert modes.style_for("continuous", "balanced") == "cinematic" and modes.style_for("continuous", "minimal") == "minimal"
    assert all(modes.style_for(m, e) in STYLES for m in modes.MODES for e in modes.EDIT)


def test_effects_off_and_scaled():
    lim = STYLES["viral"]["limits"]
    off = modes.effective_limits({"zoom": "off", "sfx": "off"}, lim)
    assert off["zoom_max"] == 0 and off["sfx_max"] == 0
    hi = modes.effective_limits({"zoom": "high"}, lim)
    assert hi["zoom_per_s"] > lim["zoom_per_s"] and hi["zoom_scale_cap"] == 1.45
    assert modes.effective_limits({"mode": "continuous"}, lim)["overlays"] == 0
    assert modes.effective_limits(None, lim) == lim


def test_check_drops_effects_the_owner_turned_off_without_an_error():
    e = edl(zooms=[zoom(20), zoom(40), zoom(60)], sfx=[sfx(20), sfx(40), sfx(60), sfx(70)])
    errors, warnings, out = check(e, 10, 90, job_meta={"zoom": "off", "sfx": "off"})
    assert not errors and out.zooms == [] and out.sfx == []
    assert any("turned off" in w for w in warnings)


def test_zoom_intensity_low_caps_scale():
    e = edl(zooms=[zoom(30, 1.45)])
    errors, warnings, out = check(e, 10, 90, job_meta={"zoom": "low"})
    assert not errors and out.zooms[0].scale == 1.15


def test_rules_per_mode():
    cont = modes.director_rule({"mode": "continuous"})
    assert "ONE continuous stretch" in cont and "No cold_open" in cont and "never inside dialogue" in cont
    story = modes.story_rule({"mode": "continuous", "length": [60, 120]})
    assert "SINGLE beat" in story and "NEVER combine distant parts" in story and "60-120 seconds" in story
    assert "RELATED moments" in modes.story_rule({"mode": "highlights"})
    c = modes.story_rule({"mode": "custom", "instructions": "end on a cliffhanger"})
    assert "end on a cliffhanger" in c and "end on a cliffhanger" in modes.director_rule({"mode": "custom", "instructions": "end on a cliffhanger"})
    assert 'captions.style must be "karaoke"' in modes.director_rule({"mode": "viral", "caption_look": "clean"})


def test_silence_sensitivity():
    assert modes.silence_gap({"silence": "off"}) is None
    assert modes.silence_gap({}) is False  # not chosen: Settings decide
    assert modes.silence_gap({"silence": "low"}) > modes.silence_gap({"silence": "high"})


def test_deadair_gap_is_adjustable():
    words = [{"s": 0.0, "e": 1.0, "w": "hi"}, {"s": 5.0, "e": 6.0, "w": "there"}]  # a 4 s silence
    quiet = [-60.0] * 100
    args = (0.0, 6.0, words, quiet, 0.1, [])
    assert deadair.dead_stretches(*args, gap_min=1.8) and deadair.dead_stretches(*args, gap_min=3.0)
    assert not deadair.dead_stretches(*args, gap_min=4.5)  # low sensitivity leaves a 4 s pause alone
