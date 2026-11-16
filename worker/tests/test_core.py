"""Offline unit tests: no network, no database, no Gemini."""
import json
import wave
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from clipper import analysis, gemini, ingest, qc
from clipper.edl import EDL, check, to_output_time
from clipper.styles import STYLES


def base_edl(**over):
    d = {"title": "Test clip", "description": "desc", "hook": {"text": "Watch this"},
         "segments": [{"start": 10, "end": 20, "reason": "setup of the moment"}, {"start": 22, "end": 30, "reason": "payoff and reaction"}],
         "layout": {"mode": "fit"}}
    d.update(over)
    return EDL.model_validate(d)


def test_output_time_mapping():
    e = base_edl()
    assert to_output_time(e.segments, 10) == 0
    assert to_output_time(e.segments, 15) == 5
    assert to_output_time(e.segments, 21) is None  # inside the cut
    assert to_output_time(e.segments, 22) == 10


def test_check_drops_effects_inside_cuts():
    e = base_edl(sfx=[{"at": 21, "sound": "boom", "reason": "impact on the hit"}])
    errors, warnings, out = check(e, 9, 31)
    assert not errors and out.sfx == [] and any("falls in a cut" in w for w in warnings)


def test_check_rejects_overlap_and_window():
    e = base_edl(segments=[{"start": 10, "end": 20, "reason": "first part of it"}, {"start": 18, "end": 25, "reason": "overlapping part"}])
    errors, _, _ = check(e, 9, 31)
    assert any("overlap" in x for x in errors)
    errors, _, _ = check(base_edl(), 15, 31)
    assert any("clip window" in x for x in errors)


@pytest.mark.parametrize("style", list(STYLES))
def test_style_limits_enforced(style):
    zooms = [{"start": 10 + i, "end": 10.8 + i, "target": "gameplay", "scale": 1.5, "reason": "punch on the action"} for i in range(9)]
    e = base_edl(style=style, zooms=zooms)
    errors, _, _ = check(e, 9, 31)
    assert any("too many zooms" in x for x in errors) or any("zoom scale" in x for x in errors)


def test_minimal_rejects_overlays_and_cinematic_rejects_emoji():
    ov = [{"kind": "emoji", "start": 12, "end": 13, "value": "😱", "reason": "mirrors his panic"}]
    assert any("overlays" in x for x in check(base_edl(style="minimal", overlays=ov), 9, 31)[0])
    assert any("emoji" in x for x in check(base_edl(style="cinematic", overlays=ov), 9, 31)[0])
    assert not check(base_edl(style="viral", overlays=ov), 9, 31)[0]


def test_schema_rejects_bad_values():
    with pytest.raises(ValidationError):
        base_edl(layout={"mode": "split"})  # split needs facecam_box
    with pytest.raises(ValidationError):
        base_edl(sfx=[{"at": 12, "sound": "airhorn", "reason": "not an allowed sound"}])
    with pytest.raises(ValidationError):
        base_edl(zooms=[{"start": 11, "end": 12, "target": "gameplay", "scale": 3.0, "reason": "far too much zoom"}])


def test_youtube_id_validation():
    assert ingest.youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert ingest.youtube_id("https://youtu.be/dQw4w9WgXcQ?t=10") == "dQw4w9WgXcQ"
    assert ingest.youtube_id("https://www.youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    for bad in ("https://evil.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=short", "javascript:alert(1)", ""):
        assert ingest.youtube_id(bad) is None


def test_twitch_vod_id_validation():
    assert ingest.twitch_id("https://www.twitch.tv/videos/1234567890") == "1234567890"
    assert ingest.twitch_id("https://m.twitch.tv/someone/video/987654321?t=1h2m") == "987654321"
    for bad in ("https://www.twitch.tv/someone", "https://evil.com/videos/123", "javascript:alert(1)", ""):
        assert ingest.twitch_id(bad) is None


def test_twitch_chat_rows_feed_existing_spike_detector(tmp_path):
    from clipper import signals
    p = tmp_path / "source.twitch_chat.jsonl"
    rows = [{"t": i, "text": "normal"} for i in range(30)]
    rows += [{"t": 40, "text": "NO WAY LMAO"} for _ in range(20)]
    p.write_text("\n".join(json.dumps(x) for x in rows))
    assert len(signals.parse_live_chat(p)) == 50
    activity = signals.chat_activity(p, 60)
    assert activity and activity["messages"] == 50
    assert max(activity["hype"]) == 20


def test_inbox_traversal_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.config, "INBOX", tmp_path)
    (tmp_path / "ok.mp4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert ingest.inbox_file("ok.mp4").name == "ok.mp4"
    assert ingest.inbox_file("../../ok.mp4").name == "ok.mp4"  # basename only, stays in inbox
    with pytest.raises(ingest.Unrecoverable):
        ingest.inbox_file("../../.env")
    with pytest.raises(ingest.Unrecoverable):
        ingest.inbox_file("notes.txt")


def test_extract_json_handles_fences_and_blocks():
    resp = {"candidates": [{"content": {"parts": [{"text": "```json\n{\"a\": 1}\n```"}]}}]}
    assert gemini.extract_json(resp) == {"a": 1}
    with pytest.raises(ValueError):
        gemini.extract_json({"promptFeedback": {"blockReason": "SAFETY"}})


def _wav(path, sr, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def test_candidates_find_loud_scream_peak(tmp_path):
    sr, dur = 16000, 180
    t = np.arange(sr * dur) / sr
    x = 0.01 * np.sin(2 * np.pi * 200 * t)
    burst = (t > 120) & (t < 124)
    x[burst] += 0.8 * np.sin(2 * np.pi * 3000 * t[burst])  # loud, high-frequency "scream"
    _wav(tmp_path / "a.wav", sr, x)
    feats = analysis.audio_features(tmp_path / "a.wav")
    tr = {"words": [], "segments": []}
    tl = analysis.score_timeline(dur, feats, [], tr)
    cands = analysis.pick_candidates(dur, tl, tr, 3)
    assert cands and any(c["start"] <= 121 <= c["end"] for c in cands)
    assert all(15 <= c["duration"] <= 75 for c in cands)


def test_qc_check_on_generated_clip(tmp_path):
    import subprocess
    f = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-af", "volume=0.3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(f)], check=True)
    props = {"duration": 3.0, "hook": {"text": "hi"}, "captions": {"words": [{"w": "hello", "s": 0.1, "e": 0.5}, {"w": "supercalifragilistic", "s": 1, "e": 2}]}}
    res = qc.check_file(f, props)
    assert res["checks"]["dimensions"]["ok"] and res["checks"]["fps"]["ok"] and res["checks"]["av_sync"]["ok"]
    assert not res["checks"]["caption_safe_area"]["ok"]  # 20-char word cannot fit


def test_ingest_ignores_partial_downloads(tmp_path):
    from clipper.ingest import finished_source
    (tmp_path / "source.f399.mp4.part").write_bytes(b"x")
    (tmp_path / "source.f251.webm").write_bytes(b"x")
    assert finished_source(tmp_path) is None
    (tmp_path / "source.mp4").write_bytes(b"x")
    assert finished_source(tmp_path).name == "source.mp4"


def test_caption_fixes_merge_and_keep_punctuation():
    from clipper.edl import CaptionFix
    from clipper.render import apply_caption_fixes
    words = [{"w": "Welcome", "s": 0.0, "e": 0.4}, {"w": "Hamlu", "s": 0.5, "e": 0.8},
             {"w": "nation,", "s": 0.8, "e": 1.2}, {"w": "guns", "s": 1.3, "e": 1.6}]
    out = apply_caption_fixes(words, [CaptionFix(wrong="hamlu nation", right="Vice City")])
    assert [w["w"] for w in out] == ["Welcome", "Vice", "City,", "guns"]
    assert out[1]["s"] == 0.5 and out[2]["e"] == 1.2 and out[1]["e"] == out[2]["s"]
    assert apply_caption_fixes(words, []) == words


def test_rank_schema_requires_content_type():
    from clipper.gemini import RANK_SCHEMA, CONTENT_GUIDE
    assert "content_type" in RANK_SCHEMA["required"]
    assert set(RANK_SCHEMA["properties"]["content_type"]["enum"]) == set(CONTENT_GUIDE)


def test_edl_accepts_caption_fixes():
    from clipper.edl import EDL
    import json, pathlib
    ex = next(pathlib.Path(__file__).parent.glob("*.json"), None)
    assert "caption_fixes" in EDL.model_json_schema()["properties"]


def test_social_copy_always_returns_five_clean_hashtags():
    from clipper.edl import social_copy
    caption, tags = social_copy({"description": "He never saw this coming", "hashtags": ["#FC27", "speed", "speed", "bad tag!"]})
    assert caption == "He never saw this coming"
    assert tags[:3] == ["#FC27", "#speed", "#badtag"]
    assert len(tags) == 5 and len({x.lower() for x in tags}) == 5


def _story_edl(segments):
    from clipper.edl import EDL
    return EDL.model_validate({"title": "Heist goes wrong", "description": "A plan, a crash, a scream.",
        "hook": {"text": "He did NOT see this coming"}, "layout": {"mode": "fit"}, "segments": segments})


def test_cold_open_and_reordering():
    from clipper.edl import check, to_output_time
    r = "teaser of the crash payoff"
    edl = _story_edl([{"start": 150, "end": 152.5, "role": "cold_open", "reason": r},
                      {"start": 110, "end": 140, "reason": "setup of the heist plan"},
                      {"start": 145, "end": 165, "reason": "the crash and the scream"}])
    errors, _, out = check(edl, 100, 170, (45, 60))
    assert errors == []
    assert [s.role for s in out.segments] == ["cold_open", "main", "main"]  # order kept
    assert to_output_time(out.segments, 110) == 2.5        # story starts after the teaser
    assert to_output_time(out.segments, 150) == 2.5 + 30 + 5  # payoff maps to the main occurrence


def test_cold_open_rules_and_length():
    from clipper.edl import check
    r = "teaser of the crash payoff"
    late = _story_edl([{"start": 110, "end": 140, "reason": r}, {"start": 150, "end": 152, "role": "cold_open", "reason": r}])
    assert any("FIRST" in e for e in check(late, 100, 170)[0])
    short = _story_edl([{"start": 110, "end": 130, "reason": r}])
    assert any("45-60" in e for e in check(short, 100, 170, (45, 60))[0])
    overlap = _story_edl([{"start": 110, "end": 140, "reason": r}, {"start": 130, "end": 150, "reason": r}])
    assert any("overlap" in e for e in check(overlap, 100, 170)[0])


def test_prices_follow_the_schedule():
    import datetime
    from clipper import config
    assert config.price("gemini-3.8-flash", datetime.date(2026, 12, 31)) == (0.75, 3.75)
    assert config.price("gemini-3.8-flash", datetime.date(2027, 1, 1)) == (1.50, 7.50)
    assert config.price("gemini-3.1-flash-lite") == (0.25, 1.50)


def test_body_sets_fps_and_low_resolution(tmp_path):
    from clipper.gemini import _body
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    b = _body(v, "hi", None, 0.2, fps=2, low_res=True)
    assert b["contents"][0]["parts"][0]["video_metadata"] == {"fps": 2}
    assert b["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert "video_metadata" not in _body(v, "hi")["contents"][0]["parts"][0]


def _words(*spec):
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]


def test_snap_never_cuts_inside_a_word_and_prefers_sentence_ends():
    from clipper.timing import boundaries, snap_point
    words = _words(("We", 10.0, 10.2), ("got", 10.25, 10.5), ("this.", 10.55, 11.0), ("Save", 11.6, 11.9), ("my", 11.95, 12.1), ("city", 12.15, 12.6))
    b = boundaries(words, [])
    t, note = snap_point(10.4, "end", words, b)  # AI cut inside "got"
    assert t >= 11.0 and note == "sentence end"  # moved to after "this."
    t, _ = snap_point(11.7, "start", words, b)  # AI start inside "Save"
    assert t <= 11.6


def test_scene_change_wins_when_nobody_talks():
    from clipper.timing import boundaries, snap_point
    words = _words(("Go", 5.0, 5.3), ("now", 8.0, 8.3))
    t, note = snap_point(6.3, "end", words, boundaries(words, [6.5]))
    assert t == 6.5 and note == "scene change"


def test_ends_mid_sentence():
    from clipper.timing import ends_mid_sentence
    words = _words(("We", 1.0, 1.2), ("got", 1.25, 1.5), ("this.", 1.55, 2.0), ("Next", 2.6, 2.9))
    assert ends_mid_sentence(1.5, words)
    assert not ends_mid_sentence(2.1, words)


def test_snap_segments_keeps_main_segments_apart():
    from clipper.timing import snap_segments
    words = _words(("a.", 1.0, 1.4), ("b.", 3.0, 3.4), ("c.", 5.0, 5.4))
    segs = [{"start": 0.9, "end": 3.2, "reason": "x" * 8}, {"start": 3.1, "end": 5.5, "reason": "x" * 8}]
    out, _ = snap_segments(segs, words, [], (0, 10))
    assert out[0]["end"] <= out[1]["start"]


def test_refine_words_finds_real_onsets(tmp_path):
    import wave
    import numpy as np
    from clipper.timing import refine_words
    sr = 16000
    x = np.random.default_rng(0).normal(0, 0.002, sr * 4)
    for onset in (1.0, 2.5):  # two "words": 300 ms bursts in the speech band
        t = np.arange(int(0.3 * sr)) / sr
        x[int(onset * sr):int(onset * sr) + len(t)] += 0.3 * np.sin(2 * np.pi * 800 * t)
    p = tmp_path / "a.wav"
    with wave.open(str(p), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((x * 32767).astype(np.int16).tobytes())
    whisper = _words(("one", 1.09, 1.25), ("two", 2.42, 2.7))  # typical Whisper error: ~0.1 s off
    out = refine_words(whisper, p, 0, 4)
    assert abs(out[0]["s"] - 1.0) <= 0.03 and abs(out[1]["s"] - 2.5) <= 0.03


def test_music_licences_allow_only_commercial_safe():
    from clipper.music import licence_ok
    for ok in ("cc0", "pdm", "by", "by-sa", "CC0 1.0", "Creative Commons 0", "http://creativecommons.org/licenses/by/4.0/",
               "http://creativecommons.org/licenses/by-sa/3.0/", "https://creativecommons.org/publicdomain/zero/1.0/"):
        assert licence_ok(ok), ok
    for bad in ("by-nc", "by-nd", "by-nc-sa", "http://creativecommons.org/licenses/by-nc-nd/3.0/", "", None, "sampling+", "all rights reserved"):
        assert not licence_ok(bad), bad


def test_music_falls_back_to_openverse_without_keys(monkeypatch, tmp_path):
    from clipper import config, music
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)
    monkeypatch.setattr(config, "ASSET_CACHE", tmp_path)
    calls = []
    monkeypatch.setattr(music, "openverse", lambda q, s: calls.append(q) or {"file": tmp_path / "x.mp3", "provider": "openverse (jamendo)",
                        "title": "t", "author": "a", "license": "CC BY 4.0", "source": "https://example.org"})
    m = music.pick("tense", 30)
    assert calls == ["tense suspense"] and m["license"] == "CC BY 4.0"
    assert music.keys() == {"freesound": False, "jamendo": False, "openverse": True}


def _box(text, x=0.3, y=0.82, w=0.4, h=0.05, conf=0.9):
    return {"text": text, "conf": conf, "x": x, "y": y, "w": w, "h": h}


def test_burned_captions_found_when_centred_text_keeps_changing():
    from clipper.burned import analyse
    frames = [[_box(f"line number {i} here")] if i % 3 == 0 else [] for i in range(40)]
    r = analyse(frames)
    assert r["present"] and r["where"] == "bottom"


def test_burned_captions_ignore_hud_chat_and_small_text():
    from clipper.burned import analyse
    hud = [[_box("AMMO 30 / 90")] for _ in range(40)]  # same text every frame: a HUD, not captions
    chat = [[_box(f"viewer{i}: lol this is crazy", x=0.3)] for i in range(40)]  # chat lines
    small = [[_box(f"Subscribe to the list {i}", h=0.015)] for i in range(40)]  # web page text
    corner = [[_box(f"Score {i} points", x=0.02, w=0.2)] for i in range(40)]  # not centred
    for frames in (hud, chat, small, corner):
        assert not analyse(frames)["present"]


def _fb_env(monkeypatch, openrouter=None, groq=None):
    from clipper import budget, db, fallback
    monkeypatch.setattr(budget, "blocked", lambda *a, **k: None)
    monkeypatch.setattr(db, "event", lambda *a, **k: None)
    monkeypatch.setattr(fallback, "keys", lambda: {"openrouter": openrouter is not None, "groq": groq is not None})
    if openrouter:
        monkeypatch.setattr(fallback, "openrouter", openrouter)
    if groq:
        monkeypatch.setattr(fallback, "groq", groq)
    return fallback


_REQ = {"model": "gemini-3.8-flash", "body": {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {}}}
_QUOTA = {"error": {"message": "The service is receiving too many requests from you", "description": "You exceeded your current quota"}}


def test_fallback_passes_good_answers_through(monkeypatch):
    fb = _fb_env(monkeypatch)
    good = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    assert fb.rescue("j", "rank", good, lambda: 1 / 0) is good  # never rebuilds or calls anyone


def test_fallback_rescues_quota_errors_with_openrouter(monkeypatch):
    seen = []
    fb = _fb_env(monkeypatch, openrouter=lambda body, model: seen.append(model) or fb_answer("openrouter:" + model))
    out = fb.rescue("j", "director", _QUOTA, lambda: _REQ)
    assert seen == ["google/gemini-3.8-flash"] and out["modelVersion"] == "openrouter:google/gemini-3.8-flash"


def fb_answer(model):
    return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}], "modelVersion": model, "costUsd": 0.01}


def test_fallback_uses_groq_when_openrouter_fails(monkeypatch):
    def broken(body, model):
        raise RuntimeError("OpenRouter HTTP 503")
    fb = _fb_env(monkeypatch, openrouter=broken, groq=lambda body, model: fb_answer("groq:" + model))
    assert fb.rescue("j", "rank", _QUOTA, lambda: _REQ)["modelVersion"].startswith("groq:")


def test_fallback_never_masks_non_retryable_errors(monkeypatch):
    fb = _fb_env(monkeypatch, openrouter=lambda b, m: fb_answer("x"))
    bad_request = {"error": {"message": "400 Invalid JSON payload", "httpCode": "400"}}
    assert fb.rescue("j", "rank", bad_request, lambda: _REQ) is bad_request


def test_fallback_reports_every_failure_without_keys(monkeypatch):
    fb = _fb_env(monkeypatch)
    out = fb.rescue("j", "rank", _QUOTA, lambda: _REQ)
    assert "openrouter: no key" in out["error"]["message"] and "groq: no key" in out["error"]["message"]


def test_gemini_schema_converts_to_json_schema():
    from clipper.fallback import to_json_schema
    from clipper.gemini import RANK_SCHEMA
    s = to_json_schema(RANK_SCHEMA)
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert s["properties"]["quality"]["type"] == "number"
    assert s["properties"]["facecam_box"]["type"] == ["object", "null"]


def test_sound_library_variants_and_credits():
    from clipper.sfxlib import credit, file_for, names
    assert "punch" in names() and "whoosh" in names()
    assert file_for("whoosh", 3.0) == "sfx/whoosh.wav"
    a, b = file_for("punch", 1.0), file_for("punch", 1.0)
    assert a == b and a.startswith("sfx/kenney/punch_")  # stable per moment
    assert len({file_for("punch", t / 2) for t in range(20)}) > 1  # variety across moments
    assert credit(a)["license"] == "CC0 1.0"
    from pathlib import Path
    from clipper import config
    assert (config.RENDERER / "public" / a).is_file()


def test_edl_accepts_library_sounds():
    from clipper.edl import EDL
    e = EDL.model_validate({"title": "Big crash", "description": "A car crash.", "hook": {"text": "Watch this crash"},
                            "layout": {"mode": "fit"}, "segments": [{"start": 1, "end": 20, "reason": "the crash moment"}],
                            "sfx": [{"at": 5, "sound": "hit_metal", "reason": "the car hits the wall"}]})
    assert e.sfx[0].sound == "hit_metal"


def test_trends_game_detection():
    from clipper.trends import game_of
    assert game_of("Cinna reacts to GTA 6 Trailer 2") == "GTA 6"
    assert game_of("Grand Theft Auto VI Trailer 2") == "GTA 6"
    assert game_of("GTA 5 Free Roam is Always Hilarious") == "GTA 5"
    assert game_of("Oroboro's Dark Souls II Parry Tips") == "Dark Souls"
    assert game_of("my vacation vlog") is None


def test_trends_skip_without_keys_and_use_cache(monkeypatch, tmp_path):
    from clipper import config, db, trends
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(db, "add_cost", lambda *a, **k: None)
    monkeypatch.delenv("SHORTSINTEL_API_KEY", raising=False)
    monkeypatch.delenv("TRENDSMCP_API_KEY", raising=False)
    assert "no key" in trends.hints("j", "GTA 6")["skipped"]
    assert trends.hints("j", None)["skipped"].startswith("no game")
    monkeypatch.setenv("SHORTSINTEL_API_KEY", "x")
    calls = []
    monkeypatch.setattr(trends, "shortsintel", lambda g: calls.append(g) or {"provider": "shortsintel", "game": g, "hashtags": ["#gta6"], "phrases": ["leak"], "credits": 3})
    first, second = trends.hints("j", "GTA 6"), trends.hints("j", "GTA 6")
    assert calls == ["GTA 6"] and not first["cached"] and second["cached"]  # one paid lookup per game per day
    assert "#gta6" in trends.prompt_section(first) and trends.prompt_section({"skipped": "x"}) == ""


def test_shortsintel_response_becomes_hints(monkeypatch):
    import httpx
    from clipper import trends
    monkeypatch.setenv("SHORTSINTEL_API_KEY", "x")
    payload = {"run": {"charge_credits": 3}, "result": {"trend": {"direction": "rising"}, "stats": {"sampled_videos": 2}, "top_videos": [
        {"hashtags": ["GTA6", "fyp", "vicecity"], "engagement": {"views": 7_000_000}, "description": "POV: it's release day #gta6 #fyp"},
        {"hashtags": [], "engagement": {"views": 100}, "description": "Vice City heist map leak #gtaleaks https://x.y/z"}]}}
    seen = {}
    monkeypatch.setattr(httpx, "get", lambda url, **k: seen.update(url=url) or httpx.Response(200, json=payload, request=httpx.Request("GET", "https://x")))
    h = trends.shortsintel("GTA 6")
    assert seen["url"].endswith("/tiktok") and h["platform"] == "tiktok"
    assert h["hashtags"][:2] == ["#gta6", "#vicecity"] and "#fyp" not in h["hashtags"] and "#gtaleaks" in h["hashtags"]
    assert h["examples"][0] == {"text": "POV: it's release day", "views": 7_000_000}
    assert h["examples"][1]["text"] == "Vice City heist map leak" and h["trend"] == "rising"
    sec = trends.prompt_section({**h, "game": "GTA 6"})
    assert "#vicecity" in sec and "release day" in sec and "word for word" in sec


def test_moderation_flags_only_real_problems():
    from clipper.gemini import moderation_flags
    gta = {"sexual": "none", "gore": "medium", "hate": "none", "self_harm": "none", "drugs": "low"}  # normal game violence
    assert moderation_flags(gta) == []
    assert moderation_flags({**gta, "sexual": "medium"}) == ["sexual (medium)"]
    assert moderation_flags({**gta, "gore": "high", "hate": "high"}) == ["gore (high)", "hate (high)"]


def test_moderation_failure_blocks_the_clip(monkeypatch):
    from clipper import db, gemini
    stored = {}
    clip = {"qc": {"passed": True, "fails": []}, "edl": {"x": 1}, "status": "ready_for_review"}
    monkeypatch.setattr(gemini, "record_cost", lambda *a, **k: None)
    monkeypatch.setattr(db, "event", lambda *a, **k: None)
    monkeypatch.setattr(db, "q1", lambda *a, **k: clip)
    monkeypatch.setattr(db, "q", lambda sql, args: stored.update(qc=args[0], status=args[1]))
    resp = {"candidates": [{"content": {"parts": [{"text": '{"sexual":"high","gore":"none","hate":"none","self_harm":"none","drugs":"none","issues":["0:03 nudity"]}'}]}}]}
    r = gemini.save_moderation("j", 0, resp)
    assert r["flagged"] and stored["status"] == "qc_failed"
    assert "moderation: sexual (high)" in stored["qc"].obj["fails"]


def test_moderation_is_off_by_default():
    from clipper.manage import Settings
    assert Settings().moderation_enabled is False and Settings().publishing_enabled is False


def test_publish_gate_requires_manual_steps():
    from clipper.publish import NotIntegrated, gate

    class Ready:
        name, configured = "ayrshare", staticmethod(lambda: True)
    draft = {"status": "draft"}
    assert gate("send", draft, True, Ready()) == "Approve the post first."
    assert gate("send", {"status": "approved"}, False, Ready()) is None  # sending a held draft does not need publishing on
    assert "locked" in gate("publish", {"status": "sent", "external_id": "x"}, False, Ready())
    assert gate("publish", {"status": "approved"}, True, Ready()) == "Send the post as a draft first."
    assert gate("publish", {"status": "sent", "external_id": "x"}, True, Ready()) is None
    assert "not configured" in gate("send", {"status": "approved"}, True, NotIntegrated("postlake"))


def test_ayrshare_creates_a_held_post(monkeypatch, tmp_path):
    import httpx
    from clipper.publish import Ayrshare
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0" * 1000)
    sent = {}

    def fake_post(url, **kw):
        sent.setdefault("urls", []).append(url)
        if url.endswith("/media/upload"):
            return httpx.Response(200, json={"url": "https://cdn.example/clip.mp4"}, request=httpx.Request("POST", url))
        sent["body"] = kw["json"]
        return httpx.Response(200, json={"id": "p123", "status": "awaiting approval"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    r = Ayrshare("k").create_draft({"id": "1", "platform": "youtube", "title": "T", "caption": "C", "hashtags": "#gta6"}, video)
    assert r == {"external_id": "p123", "status": "awaiting approval", "media_url": "https://cdn.example/clip.mp4"}
    b = sent["body"]
    assert b["requiresApproval"] is True and b["platforms"] == ["youtube"] and b["mediaUrls"] == ["https://cdn.example/clip.mp4"]
    assert b["post"] == "C\n\n#gta6" and b["youTubeOptions"]["title"] == "T"


def test_unknown_publishers_refuse_to_run(tmp_path):
    import pytest
    from clipper.publish import PublishError, provider
    with pytest.raises(PublishError):
        provider("publora").create_draft({}, tmp_path / "x.mp4")


# ---------- streamer signals ----------

def test_live_chat_parsing_and_lag(tmp_path):
    import json
    from clipper import signals
    lines = []
    for i in range(40):
        t = 10_000 + i * 100 if i < 30 else 50_000
        lines.append(json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(t), "actions": [{"addChatItemAction": {"item": {
            "liveChatTextMessageRenderer": {"message": {"runs": [{"text": "KEKW no way" if i % 2 else "hi"}]}}}}}]}}))
    lines.append("not json")
    p = tmp_path / "source.live_chat.json"
    p.write_text("\n".join(lines))
    msgs = signals.parse_live_chat(p)
    assert len(msgs) == 40 and msgs[0] == (10.0, "hi")
    ch = signals.chat_activity(p, 60)
    assert ch["messages"] == 40
    assert sum(ch["rate"][5:9]) == 30  # 10-12.9 s shifted 5 s earlier
    assert ch["hype"][5] > 0 and sum(ch["hype"]) == 20
    assert signals.chat_activity(tmp_path / "missing.json", 60) is None


def test_peaks_finds_spikes_apart():
    from clipper.signals import peaks
    v = [1.0] * 100
    v[20], v[22], v[70] = 30, 25, 40
    assert peaks(v, 3.0, gap=6) == [20, 70]
    assert peaks([0] * 50) == []


def test_refine_cam_box_finds_still_webcam_region():
    import numpy as np
    from clipper.signals import refine_cam_box
    rng = np.random.default_rng(1)
    frames = rng.integers(0, 255, (20, 90, 160)).astype(np.uint8)  # busy gameplay everywhere
    frames[:, 0:30, 0:50] = 120 + rng.integers(0, 4, (20, 30, 50))  # still webcam in the top-left corner
    face = {"x": 15 / 160, "y": 8 / 90, "w": 10 / 160, "h": 12 / 90}
    guess = {"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.4}
    b = refine_cam_box(frames, face, guess)
    assert b["method"] == "still-region"
    assert abs(b["x"]) < 0.02 and abs(b["y"]) < 0.03 and abs(b["w"] - 50 / 160) < 0.03 and abs(b["h"] - 30 / 90) < 0.04
    flat = np.full((20, 90, 160), 100, np.uint8)  # nothing moves: no reliable border -> keep the guess
    assert refine_cam_box(flat, face, guess)["method"] == "guess"


def test_score_timeline_uses_reaction_and_chat():
    import numpy as np
    from clipper.analysis import score_timeline
    n = 60
    feats = {"hop": 0.5, "rms_db": [-30.0] * (2 * n), "hf_ratio": [0.1] * (2 * n)}
    tr = {"words": [], "segments": []}
    base = score_timeline(n, feats, [], tr)
    fm = [1.0] * n
    fm[30] = 50.0
    chat = {"rate": [1.0] * n, "hype": [0.0] * n}
    chat["rate"][40] = 40.0
    chat["hype"][40] = 30.0
    tl = score_timeline(n, feats, [], tr, fm, chat)
    assert "reaction" in tl["components"] and "chat" in tl["components"]
    assert tl["score"][30] > base["score"][30] and tl["score"][40] > tl["score"][10]
    assert int(np.argmax(tl["score"])) in range(37, 44)


# ---------- bubble layout ----------

def test_bubble_layout_needs_facecam_box():
    import pytest
    from pydantic import ValidationError
    from clipper.edl import Layout
    lay = Layout(mode="bubble", facecam_box={"x": 0, "y": 0, "w": 0.27, "h": 0.27}, bubble_corner="mid_left", bubble_shape="rounded")
    assert lay.bubble_size == 0.34
    with pytest.raises(ValidationError):
        Layout(mode="bubble")


# ---------- fast renderer ----------

def test_fast_zoom_expression_matches_zoom_curve():
    from clipper.fastrender import zoom_expr

    def ev(expr, t):
        import math
        e = (expr.replace("between", "_b").replace("if(", "_if(").replace("min(", "min(").replace("pow(", "pow("))
        return eval(e, {"t": t, "_b": lambda x, a, b: a <= x <= b, "_if": lambda c, a, b: a if c else b, "min": min, "pow": pow, "math": math})
    z = [{"start": 2.0, "end": 4.0, "target": "facecam", "scale": 1.5, "fx": 0.2, "fy": 0.3, "style": "smooth"}]
    s, fx, fy = zoom_expr(z, ("facecam",), [])
    assert ev(s, 1.0) == 1 and abs(ev(s, 3.5) - 1.5) < 1e-6 and 1 < ev(s, 2.5) < 1.5 and ev(s, 4.5) == 1
    assert ev(fx, 3.0) == 0.2 and ev(fy, 3.0) == 0.3 and ev(fx, 5.0) == 0.5
    s2, _, _ = zoom_expr(z, ("gameplay",), [{"at": 1.0, "kind": "zoom_punch"}])
    assert abs(ev(s2, 1.0) - 1.14) < 1e-6 and ev(s2, 3.0) == 1  # the facecam zoom does not move the gameplay


def test_fast_cover_rect_and_mask(tmp_path):
    from clipper.fastrender import cover_rect, write_mask
    assert cover_rect(1920, 1080, 1080, 1920, None, 0.5) == (656, 0, 606, 1080)
    assert cover_rect(1920, 1080, 1080, 1920, None, 0.0)[0] == 0
    x, y, w, h = cover_rect(1920, 1080, 600, 600, {"x": 0, "y": 0, "w": 0.27, "h": 0.27})
    assert y == 0 and abs(w - h) <= 2 and abs((x + w / 2) - 0.135 * 1920) < 4  # square crop centred on the wide cam
    write_mask(tmp_path / "m.pgm", 40, 40, 20)
    data = (tmp_path / "m.pgm").read_bytes()
    px = data[data.index(b"255\n") + 4:]
    assert len(px) == 1600 and px[20 * 40 + 20] == 255 and px[0] == 0


def test_fast_ass_captions_hook_and_stickers():
    from clipper.fastrender import build_ass, group_words, duck_expr
    words = [{"w": "no", "s": 0.5, "e": 0.7}, {"w": "way", "s": 0.75, "e": 1.0}, {"w": "bro!", "s": 1.05, "e": 1.4},
             {"w": "look", "s": 3.0, "e": 3.3}]
    assert [len(g) for g in group_words(words)] == [3, 1]
    p = {"style": "viral", "layout": {"mode": "full", "facecamShare": 0.22}, "captions": {"enabled": True, "style": "pop",
         "position": "lower", "emphasis": ["WAY"], "words": words}, "hook": {"text": "He did NOT see it {coming} 💀", "duration": 2.5},
         "overlays": [{"kind": "text", "start": 1, "end": 2, "value": "INSANE 🤯", "position": "top", "animation": "pop", "file": None},
                      {"kind": "emoji", "start": 1, "end": 2, "value": "😱", "position": "top", "animation": "pop", "file": "assets/x.svg"}]}
    ass = build_ass(p, 1344)
    dl = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    cap = [l for l in dl if ",Cap," in l]
    assert len(cap) == 4  # one event per active word
    assert "NO" in cap[0] and "\\fscx82" in cap[0] and "\\fscx82" not in cap[1]
    assert "&H003B3BFF" in cap[1]  # emphasised word in the theme's hot colour (BGR)
    assert any(",Hook," in l and l.endswith("(coming)") for l in dl)  # braces escaped, emoji dropped
    st = [l for l in dl if ",Sticker," in l]
    assert len(st) == 1 and st[0].endswith("INSANE")  # emoji stripped; emoji overlay drawn as an image, not text
    assert duck_expr([]) == "0" and duck_expr(words).count("clip(") == 2  # speech merged into two regions


def test_fast_captions_never_overlap():
    from clipper.fastrender import build_ass
    words = [{"w": "see", "s": 16.65, "e": 16.8}, {"w": "you", "s": 16.82, "e": 16.9}, {"w": "brian.", "s": 16.94, "e": 17.3},
             {"w": "love", "s": 17.44, "e": 17.6}, {"w": "you.", "s": 17.66, "e": 17.7}, {"w": "dude", "s": 17.74, "e": 18.0}]
    p = {"style": "viral", "layout": {"mode": "full", "facecamShare": 0.22}, "captions": {"enabled": True, "style": "pop",
         "position": "lower", "emphasis": [], "words": words}, "hook": {}, "overlays": []}

    def secs(ts):
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    ev = [l.split(",")[1:3] for l in build_ass(p, 1344).splitlines() if ",Cap," in l]
    spans = [(secs(a), secs(b)) for a, b in ev]
    assert all(spans[i][1] <= spans[i + 1][0] + 1e-9 for i in range(len(spans) - 1))


def test_plan_retries_stop_at_the_spending_limit(monkeypatch, tmp_path):
    from clipper import budget, db, gemini
    monkeypatch.setattr(gemini, "record_cost", lambda *a, **k: None)
    monkeypatch.setattr(gemini, "_analysis", lambda j: tmp_path)
    monkeypatch.setattr(gemini, "_review_file", lambda j, i: tmp_path / "none.json")
    events, updates = [], []
    monkeypatch.setattr(db, "event", lambda *a, **k: events.append(a[3]))
    monkeypatch.setattr(db, "q1", lambda *a, **k: {"candidate": {"start": 0, "end": 30}, "output_path": "x"})
    monkeypatch.setattr(db, "q", lambda sql, args=None: updates.append(sql))
    monkeypatch.setattr(budget, "blocked", lambda j, est: "daily limit reached")
    bad = {"candidates": [{"content": {"parts": [{"text": '{"title": "cut off'}]}, "finishReason": "MAX_TOKENS"}]}
    r = gemini.save_edl("j", 0, bad)
    assert r["retry"] is False and r["attempts"] == 3  # first failure, but no budget for a retry
    assert "cut off" in r["errors"][0] and any("no more plan retries" in e for e in events)


def test_gameplay_area_drops_letterbox_bars_even_under_chat_and_cam():
    import numpy as np
    from clipper.signals import gameplay_area
    rng = np.random.default_rng(2)
    f = rng.integers(60, 255, (12, 100, 160)).astype(np.uint8)  # the game picture
    f[:, :12, :] = 5; f[:, 88:, :] = 5  # letterbox bars
    f[:, 0:8, 120:160] = 200  # chat text sitting inside the top bar
    f[:, 0:30, 0:45] = 150  # webcam over the top-left corner
    a = gameplay_area(f, {"x": 0, "y": 0, "w": 45 / 160, "h": 0.3})
    assert abs(a["y"] - 0.12) < 0.03 and abs(a["y"] + a["h"] - 0.88) < 0.03 and a["x"] == 0 and a["w"] == 1
    assert gameplay_area(rng.integers(60, 255, (12, 100, 160)).astype(np.uint8), None) is None  # no bars


def test_fast_cover_rect_stays_inside_the_game_picture():
    from clipper.fastrender import cover_rect, caption_y, CAPTION_BAR
    area = {"x": 0, "y": 0.107, "w": 1, "h": 0.786}
    x, y, w, h = cover_rect(1920, 1080, 1080, 1498, None, 0.5, area)
    assert y >= 0.107 * 1080 - 2 and y + h <= (0.107 + 0.786) * 1080 + 2
    p = {"style": "viral", "captions": {"position": "middle"}, "layout": {"mode": "split", "facecamShare": 0.22, "captionBar": True}}
    assert caption_y(p) == round(1920 * 0.22) // 2 * 2 + CAPTION_BAR / 2


def test_best_section_prefers_chat_then_audio(tmp_path):
    import json
    import numpy as np
    from clipper.signals import best_section
    assert best_section(tmp_path / "none.json", 600, 2400) == (0.0, 600, "whole video")
    lines = [json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(int(t * 1000)), "actions": [{"addChatItemAction": {"item": {
        "liveChatTextMessageRenderer": {"message": {"runs": [{"text": "LMAOOO"}]}}}}}]}}) for t in list(range(100, 7000, 50)) + [5000 + i * 0.5 for i in range(400)]]
    p = tmp_path / "chat.json"
    p.write_text("\n".join(lines))
    a, b, why = best_section(p, 9000, 2400)
    assert a <= 5000 - 5 and b >= 5200 and "chat" in why  # the window covers the chat explosion
    score = np.zeros(9000)
    score[100:200] = 50  # loud intro music: ignored
    score[7000:7300] = 5
    a, b, why = best_section(tmp_path / "none.json", 9000, 2400, score)
    assert a <= 7000 and b >= 7300 and "audio" in why


def test_slightly_long_cold_open_is_trimmed_not_rejected():
    from clipper.edl import EDL, check
    base = {"style": "viral", "title": "title", "description": "desc", "moment_type": "funny", "hook": {"text": "hook", "duration": 2},
            "layout": {"mode": "full"}, "captions": {}, "zooms": [], "overlays": [], "sfx": [], "transitions": []}
    try:
        edl = EDL.model_validate({**base, "segments": [{"start": 20, "end": 23.8, "role": "cold_open", "reason": "the teaser shot"},
                                                   {"start": 5, "end": 30, "role": "main", "reason": "the full story"}]})
    except Exception as e:  # noqa: BLE001
        raise AssertionError(e)
    errors, warnings, edl2 = check(edl, 0, 40, None)
    assert not [e for e in errors if "cold_open" in e] and any("trimmed" in w for w in warnings)
    assert abs((edl2.segments[0].end - edl2.segments[0].start) - 3.5) < 1e-6


# ---------- dead air ----------

def test_dead_air_cut_keeps_action_and_reactions():
    from clipper.deadair import cut_dead_air
    words = [{"w": "go", "s": 10.0, "e": 10.5}, {"w": "now", "s": 16.0, "e": 16.4},  # 5.5 s silent gap 10.5-16
             {"w": "wow", "s": 20.0, "e": 20.3}, {"w": "ok", "s": 26.0, "e": 26.3},   # 5.7 s gap, but loud inside
             {"w": "bye", "s": 32.0, "e": 32.5}]                                      # 5.7 s gap 26.3-32, reaction at 29
    hop = 0.5
    rms = [-30.0] * 100
    for i in range(int(22 / hop), int(23 / hop)):
        rms[i] = -5.0  # an explosion at 22 s
    segs = [{"start": 9.0, "end": 33.0, "role": "main", "reason": "the whole story"}]
    out, notes = cut_dead_air(segs, words, rms, hop, reactions=[29.0], min_total=10)
    # the two quiet gaps (10.5-16, 16.4-20) are cut; the gap with the explosion and the one with a reaction stay
    assert len(notes) == 2 and "10.9" in notes[0]
    assert [(s["start"], s["end"]) for s in out] == [(9.0, 10.9), (15.6, 16.8), (19.6, 33.0)]
    out2, notes2 = cut_dead_air(segs, words, rms, hop, reactions=[29.0], min_total=22)  # would go under 22 s
    assert notes2 == [] and out2 == segs


# ---------- transcript-first scoring ----------

def test_fuse_combines_signals_and_transcript():
    import numpy as np
    from clipper.textscore import fuse
    sig = np.zeros(100)
    sig[20] = 10  # a loud moment the transcript missed
    fused, text = fuse(sig, [{"start": 60, "end": 70, "score": 90}], 100)
    assert len(fused) == 100 and 0 <= fused.min() and fused.max() <= 100
    assert fused[65] > fused[5] and fused[20] > fused[5]  # both kinds of evidence count
    assert fused[65] > fused[20]  # a strong transcript moment beats a lone loud second
    assert text[65] == 0.9 and text[50] == 0


def test_story_candidates_keep_distant_evidence_and_drop_filler():
    from clipper.textscore import _story_candidates
    tr = {"segments": [{"s": 10, "e": 14, "text": "I bet this never happens"},
                       {"s": 500, "e": 510, "text": "It happened and I was wrong"}]}
    stories = [{"title": "The prediction", "hook": "He called it too early", "summary": "A prediction pays off",
                "score": 91, "connection": "The later result directly resolves the prediction",
                "beats": [{"start": 10, "end": 14, "role": "setup", "reason": "states the prediction", "evidence": "I bet"},
                          {"start": 500, "end": 510, "role": "payoff", "reason": "resolves it", "evidence": "I was wrong"}]}]
    out = _story_candidates(stories, 600, tr)
    assert len(out) == 1 and out[0]["start"] == 10 and out[0]["end"] == 510
    assert out[0]["duration"] == 14 and len(out[0]["story"]["beats"]) == 2


def test_story_edl_rejects_segments_from_timestamp_gaps():
    from clipper.edl import EDL, check
    base = {"style": "viral", "title": "title", "description": "desc", "moment_type": "story",
            "hook": {"text": "hook", "duration": 2}, "layout": {"mode": "full"}, "captions": {},
            "zooms": [], "overlays": [], "sfx": [], "transitions": []}
    good = EDL.model_validate({**base, "segments": [{"start": 10, "end": 15, "reason": "prediction setup"},
                                                      {"start": 500, "end": 510, "reason": "later payoff"}]})
    errors, _, _ = check(good, 10, 510, None, allowed_ranges=[(10, 15), (500, 510)])
    assert not errors
    bad = EDL.model_validate({**base, "segments": [{"start": 250, "end": 260, "reason": "unrelated filler"}]})
    errors, _, _ = check(bad, 10, 510, None, allowed_ranges=[(10, 15), (500, 510)])
    assert any("evidence-backed" in e for e in errors)


def test_text_score_request_skips_cleanly(monkeypatch, tmp_path):
    import json
    from clipper import config, textscore
    monkeypatch.setattr(config, "job_dir", lambda j: tmp_path)
    (tmp_path / "analysis").mkdir()
    assert "no analysis" in textscore.request("j")["skip"]
    (tmp_path / "analysis" / "transcript.json").write_text(json.dumps({"partial": True, "words": [], "segments": []}))
    (tmp_path / "analysis" / "timeline.json").write_text(json.dumps({"score": [0] * 10, "components": {}}))
    assert "not enough speech" in textscore.request("j")["skip"]


def test_layout_templates(monkeypatch, tmp_path):
    import json
    from clipper import config, templates
    from clipper.edl import EDL
    (tmp_path / "analysis").mkdir()
    monkeypatch.setattr(config, "job_dir", lambda j: tmp_path)
    base = {"style": "viral", "title": "title", "description": "desc", "moment_type": "funny", "hook": {"text": "hook", "duration": 2},
            "layout": {"mode": "bubble", "facecam_box": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3}}, "captions": {"enabled": False},
            "zooms": [{"start": 6, "end": 8, "target": "facecam", "scale": 1.3, "reason": "the streamer reacts here"}],
            "overlays": [], "sfx": [], "transitions": [], "segments": [{"start": 5, "end": 30, "role": "main", "reason": "the full story"}]}
    edl = EDL.model_validate(base)
    assert templates.apply_template("j", edl, {"idx": 0}, "auto") == (edl, None)  # auto: the AI's own layout
    full, note = templates.apply_template("j", edl, {"idx": 0}, "full")
    assert full.layout.mode == "full" and full.layout.facecam_box is None and full.captions.enabled and "full" in note
    assert full.zooms[0].target == "frame"
    (tmp_path / "analysis" / "facecam_clip_0.json").write_text(json.dumps({"found": True, "box": {"x": 0.02, "y": 0.6, "w": 0.2, "h": 0.3}}))
    s50 = templates.apply_template("j", edl, {"idx": 0}, "split_50")[0].layout
    assert s50.mode == "split" and s50.facecam_share == 0.5 and s50.facecam_box.x == 0.02  # the detected webcam, fixed
    assert templates.apply_template("j", edl, {"idx": 0}, "split_20")[0].layout.facecam_share == 0.2
    nocam = EDL.model_validate({**base, "layout": {"mode": "full"}})
    (tmp_path / "analysis" / "facecam_clip_0.json").unlink()
    assert templates.apply_template("j", nocam, {"idx": 0}, "split_50")[0].layout.mode == "full"  # no webcam: full screen
    assert "SPLIT" in templates.prompt_rule("split_20") and templates.prompt_rule("auto") == ""
    (tmp_path / "analysis" / "facecam_clip_0.json").write_text(json.dumps({"found": True, "box": {"x": 0.02, "y": 0.6, "w": 0.2, "h": 0.3}}))
    gta, note = templates.apply_template("j", edl, {"idx": 0}, "gta6_gameplay",
                                         {"facecam_size": "medium", "facecam_position": "top_left", "gameplay_crop": "right"})
    assert gta.layout.mode == "fit" and gta.layout.facecam_box is not None  # wide centred card on a blurred copy, webcam card on top
    assert gta.layout.gameplay_focus_x == 0.78  # the chosen crop side
    assert gta.captions.enabled and not gta.zooms and not gta.sfx and not gta.transitions and "GTA 6" in note  # captions stay on
    assert templates.apply_template("j", edl, {"idx": 0}, "gta6_gameplay", {})[0].layout.gameplay_focus_x == 0.5  # default: centred
    (tmp_path / "analysis" / "facecam_clip_0.json").unlink()
    gta_no_cam = templates.apply_template("j", nocam, {"idx": 0}, "gta6_gameplay", {"gameplay_crop": "fit"})[0]
    assert gta_no_cam.layout.mode == "fit" and gta_no_cam.layout.facecam_box is None


def test_guide_srt_follows_rough_cut_order():
    from clipper.edl import Segment
    from clipper.guide import _srt, _tc
    segs = [Segment(start=100, end=102, role="cold_open", reason="teaser of the payoff first"),
            Segment(start=10, end=12, role="main", reason="the setup of the story here")]
    words = [{"w": "setup.", "s": 10.5, "e": 11.0}, {"w": "payoff!", "s": 100.2, "e": 100.8}]
    out = _srt(words, segs)
    assert out.index("payoff!") < out.index("setup.")  # teaser plays first
    assert "00:00:02,500 --> 00:00:03,000" in out  # setup starts 2 s into the rough cut
    assert _tc(3725.0) == "1:02:05.0" and _tc(65.25) == "1:05.2"


def test_preguide_jump_labels():
    from clipper.preguide import _jump
    assert _jump(30) == ""
    assert _jump(660) == "11 MINUTES LATER"
    assert _jump(-7200) == "2 HOURS EARLIER"
    assert _jump(3000) == "1 HOUR LATER"


def test_sfx_peak_lands_on_the_action(tmp_path):
    import wave
    import numpy as np
    from clipper import sfxtime
    sr = 16000
    x = np.zeros(sr * 3, dtype=np.float32)
    x[int(1.73 * sr):int(1.9 * sr)] = 0.8 * np.sin(np.arange(int(0.17 * sr)) * 0.3)  # a hit at 1.73 s
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((x * 32767).astype(np.int16).tobytes())
    assert abs(sfxtime.onset_near(wav, 1.5) - 1.73) <= 0.02
    snd = tmp_path / "whoosh.wav"  # peak 0.3 s into the sound
    y = np.concatenate([np.linspace(0, 0.9, int(0.3 * sr)), np.linspace(0.9, 0, int(0.2 * sr))]).astype(np.float32)
    y *= np.sin(np.arange(len(y)) * 0.5)
    with wave.open(str(snd), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((y * 32767).astype(np.int16).tobytes())
    st, _ = sfxtime.align(wav, 1.5, snd)
    assert abs(st - (1.73 - 0.3)) <= 0.03  # starts early, peaks on the hit
    st2, note = sfxtime.align(wav, 1.5, snd, visual=[1.6])
    assert abs(st2 - (1.6 - 0.3)) <= 0.03 and "on-screen" in note  # tied to text popping in: locks to it
