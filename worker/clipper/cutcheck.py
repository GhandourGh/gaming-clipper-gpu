"""Cut-integrity QC: proves a finished clip's cuts are clean.

Two layers:
- edl_checks(segments, words): every main-segment boundary must sit in a word
  gap, and the clip must not END mid-sentence. Internal joins that break a
  sentence are recorded as warnings (montage/highlights jump on purpose).
  `words` are source-time transcript words. Runs in the qc stage.
- output_checks(props, duration): from render props alone (also usable offline):
  room after the last caption word, a cold open starting right on a word onset,
  and a final caption without sentence-ending punctuation.

A failing check sends the clip to qc_failed like any other QC failure; warnings
are recorded in the same checks dict with ok=True and a "warn:" detail.
"""
from . import timing

END_ROOM_FAIL = 0.35   # less room than this after the last word: fail
END_ROOM_GOOD = 0.50   # target; below it is a warning


def _res():
    return {"checks": {}, "fails": [], "warns": []}


def _add(r, name, ok, detail, warn=False):
    if ok and warn:
        r["warns"].append(name)
        r["checks"][name] = {"ok": True, "detail": f"warn: {detail}"}
    else:
        r["checks"][name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            r["fails"].append(name)


def edl_checks(segments: list[dict], words: list[dict]) -> dict:
    """Source-time checks against the planned EDL. Skips silently when there are no words."""
    r = _res()
    mains = sorted((s for s in segments if s.get("role", "main") == "main"), key=lambda s: s["start"])
    if not mains or not words:
        return r
    lo = min(s["start"] for s in mains) - 2.0
    hi = max(s["end"] for s in mains) + 2.0
    ws = [w for w in words if lo <= w["s"] and w["e"] <= hi]
    if not ws:
        return r
    inside = []
    for s in mains:
        for kind, t in (("start", s["start"]), ("end", s["end"])):
            w = timing._inside_word(t, ws)
            if w:
                inside.append(f"{kind} {t:.2f}s inside '{w['w']}'")
    _add(r, "cut_inside_word", not inside, inside or "all cuts in word gaps")
    _add(r, "ends_mid_sentence", not timing.ends_mid_sentence(mains[-1]["end"], ws),
         f"clip ends at {mains[-1]['end']:.2f}s")
    # room after the last word: fail only when the source HAD room and the plan did not take it
    E = mains[-1]["end"]
    before = [w for w in ws if w["e"] <= E + 0.02]
    if before:
        last = before[-1]
        nxt = next((w for w in ws if w["s"] > last["e"] + 0.01), None)
        clip_room = E - last["e"]
        avail = (nxt["s"] - last["e"]) if nxt else 9.9
        short = clip_room < END_ROOM_FAIL and avail >= 0.6
        _add(r, "end_room_planned", not short,
             f"{clip_room:.2f}s kept, {avail:.2f}s available after the last word",
             warn=clip_room < END_ROOM_GOOD and not short)
    joins = [f"{a['end']:.2f}s" for a, b in zip(mains, mains[1:]) if timing.ends_mid_sentence(a["end"], ws)]
    _add(r, "join_mid_sentence", True, joins or "no joins break a sentence", warn=bool(joins))
    return r


def output_checks(props: dict, duration: float) -> dict:
    """Output-time checks from render props. Caption-free clips skip the word checks."""
    r = _res()
    cap = (props or {}).get("captions") or {}
    words = cap.get("words") or []
    if not cap.get("enabled", True) or not words:
        return r
    room = duration - words[-1]["e"]
    _add(r, "end_room", room >= 0.15, f"{room:.2f}s after the last word (target {END_ROOM_GOOD}s)",
         warn=room < END_ROOM_GOOD)
    _add(r, "cold_open_mid_word", True, f"first word starts at {words[0]['s']:.2f}s",
         warn=words[0]["s"] < 0.05)
    _add(r, "final_caption_sentence", True,
         f"last word '{words[-1]['w']}'", warn=not timing.SENT_END.search(words[-1]["w"]))
    return r


def merge(res: dict, extra: dict) -> None:
    """Fold a cutcheck result into a qc.check_file result in place."""
    res["checks"].update(extra["checks"])
    for f in extra["fails"]:
        if f not in res["fails"]:
            res["fails"].append(f)
    if extra["fails"]:
        res["passed"] = False
