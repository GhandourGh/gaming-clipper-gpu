"""A plan rejected for reaching outside the story's footage gets more footage on the next attempt (4 -> 16 -> 28 -> 36 s)."""
from clipper import config, db, pipeline, preguide

BEATS = {"story": {"beats": [{"start": 1039, "end": 1051, "role": "hook"}, {"start": 1228, "end": 1234, "role": "payoff"}]}}


def test_story_ranges_grow_with_range_pad(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "q1", lambda *a, **k: {"meta": {}})
    monkeypatch.setattr(config, "job_dir", lambda _id: tmp_path)
    base = preguide.story_ranges("j", BEATS)
    wide = preguide.story_ranges("j", {**BEATS, "opts": {"range_pad": 28}})
    assert base[1][0] > 1206.72 >= wide[1][0] and wide[1][1] > base[1][1]       # the 1206.72 s start the AI asked for is now allowed
    assert wide[0][1] < wide[1][0]                                              # the two parts still do not overlap


def test_widen_ranges_steps_up_and_stops(monkeypatch):
    store = {"candidate": dict(BEATS)}
    monkeypatch.setattr(db, "q1", lambda *a, **k: dict(store))
    monkeypatch.setattr(db, "q", lambda sql, params=None: store.__setitem__("candidate", params[0]))  # J is the identity below
    monkeypatch.setattr(db, "J", lambda x: x)
    monkeypatch.setattr(db, "event", lambda *a, **k: None)
    seen = [pipeline.widen_ranges("j", 0) for _ in range(5)]
    assert seen == [16.0, 28.0, 36.0, 36.0, 36.0]
