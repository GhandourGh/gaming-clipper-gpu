from clipper import cutcheck

W = [  # a sentence, a pause, a reaction
    {"w": "He", "s": 10.0, "e": 10.2}, {"w": "jumps", "s": 10.3, "e": 10.7},
    {"w": "off.", "s": 10.8, "e": 11.2},
    {"w": "No", "s": 13.0, "e": 13.2}, {"w": "way!", "s": 13.3, "e": 13.8},
]


def test_cut_inside_word_fails():
    r = cutcheck.edl_checks([{"start": 10.5, "end": 14.5, "role": "main"}], W)
    assert "cut_inside_word" in r["fails"]
    assert "jumps" in r["checks"]["cut_inside_word"]["detail"][0]


def test_clean_cuts_pass():
    r = cutcheck.edl_checks([{"start": 9.5, "end": 14.5, "role": "main"}], W)
    assert not r["fails"]


def test_ends_mid_sentence_fails():
    ww = W[:4] + [{"w": "way", "s": 13.3, "e": 13.8}, {"w": "and", "s": 14.0, "e": 14.2}]
    r = cutcheck.edl_checks([{"start": 9.5, "end": 13.9, "role": "main"}], ww)
    assert "ends_mid_sentence" in r["fails"] and "cut_inside_word" not in r["fails"]


def test_join_mid_sentence_is_warning_only():
    ww = W[:4] + [{"w": "way", "s": 13.3, "e": 13.8}, {"w": "and", "s": 14.0, "e": 14.2},
                  {"w": "boom!", "s": 14.9, "e": 15.2}]
    segs = [{"start": 9.5, "end": 13.9, "role": "main"}, {"start": 14.5, "end": 15.6, "role": "main"}]
    r = cutcheck.edl_checks(segs, ww)
    assert "join_mid_sentence" in r["warns"] and "ends_mid_sentence" not in r["fails"]


def test_no_words_skips():
    assert cutcheck.edl_checks([{"start": 1, "end": 5, "role": "main"}], [])["checks"] == {}


def test_end_room():
    props = {"captions": {"enabled": True, "words": [{"w": "Done.", "s": 1.0, "e": 9.92}]}}
    r = cutcheck.output_checks(props, 10.0)
    assert "end_room" in r["fails"]
    r2 = cutcheck.output_checks(props, 10.45)  # 0.55s room
    assert "end_room" not in r2["fails"] and "end_room" not in r2["warns"]
    r3 = cutcheck.output_checks(props, 10.3)  # 0.40s: pass with warning
    assert "end_room" not in r3["fails"] and "end_room" in r3["warns"]


def test_cold_open_and_final_punct_warn():
    props = {"captions": {"enabled": True, "words": [{"w": "cause", "s": 0.01, "e": 0.3}, {"w": "chaos", "s": 0.4, "e": 9.0}]}}
    r = cutcheck.output_checks(props, 9.6)
    assert "cold_open_mid_word" in r["warns"] and "final_caption_sentence" in r["warns"] and not r["fails"]


def test_merge_marks_failed():
    res = {"passed": True, "fails": [], "checks": {}}
    cutcheck.merge(res, {"checks": {"x": {"ok": False, "detail": "d"}}, "fails": ["x"], "warns": []})
    assert res["passed"] is False and res["fails"] == ["x"]


def test_end_room_planned():
    ws = W[:3] + [{"w": "Quiet", "s": 15.0, "e": 15.4}]  # 3.8s gap after "off."
    r = cutcheck.edl_checks([{"start": 9.5, "end": 11.3, "role": "main"}], ws)
    assert "end_room_planned" in r["fails"]  # only 0.1s kept with room available
    r2 = cutcheck.edl_checks([{"start": 9.5, "end": 11.8, "role": "main"}], ws)
    assert "end_room_planned" not in r2["fails"]  # 0.6s kept


def test_end_room_not_failed_in_tight_dialogue():
    ws = W[:3] + [{"w": "Go", "s": 11.35, "e": 11.6}, {"w": "now!", "s": 11.7, "e": 12.0}]
    r = cutcheck.edl_checks([{"start": 9.5, "end": 11.3, "role": "main"}], ws)
    assert "end_room_planned" not in r["fails"]  # no room existed; warn at most
