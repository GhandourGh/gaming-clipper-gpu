"""Nonstop speech (no sentence punctuation) has no sentence end to finish on: the clip ends at the nearest natural pause."""
from clipper import timing


def words(spec):
    """spec: list of (word, start, end)"""
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]


NONSTOP = words([("and", 10.0, 10.3), ("then", 10.35, 10.6), ("he", 10.65, 10.8), ("just", 10.85, 11.1),
                 ("ran", 11.15, 11.4), ("away", 11.9, 12.3),  # 0.5 s pause after "ran"
                 ("from", 12.35, 12.6), ("me", 12.65, 12.9), ("bro", 12.95, 13.3), ("I", 14.2, 14.3)])  # 0.9 s pause after "bro"


def test_ends_at_the_nearest_pause_after_the_cut():
    w = timing.pause_end(NONSTOP, 8.0, 11.0)  # cut at 11.0 is inside "just": the pause after "ran" is the nearest
    assert w["w"] == "ran"


def test_falls_back_to_the_last_pause_before_the_cut_when_none_follows():
    w = timing.pause_end(NONSTOP, 8.0, 13.3, ahead=0.5)  # the only later pause is 0.9 s away: out of reach
    assert w["w"] in ("bro", "ran")  # "bro" itself ends at the cut and is followed by a pause


def test_none_when_the_speech_never_stops():
    run = words([("w%d" % i, i * 0.4, i * 0.4 + 0.38) for i in range(40)])  # gaps of 0.02 s only
    assert timing.pause_end(run, 0.0, 8.0) is None
