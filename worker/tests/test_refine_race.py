"""Several clips of one job plan at the same time and each refines a window of transcript.json: the file must stay valid
and keep every window (before: unlocked read-modify-write, so one writer's data was lost or the file was corrupted)."""
import json
import threading
import time

from clipper import analysis, config, db, media


def test_concurrent_refinements_keep_a_valid_file_with_every_window(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    (jd / "analysis").mkdir(parents=True)
    (jd / "analysis" / "audio.wav").write_bytes(b"")
    base = {"words": [{"w": "x", "s": float(i), "e": i + 0.5} for i in range(0, 600, 20)], "segments": [], "coarse": True, "refined": []}
    (jd / "analysis" / "transcript.json").write_text(json.dumps(base))
    monkeypatch.setattr(config, "job_dir", lambda _id: jd)
    monkeypatch.setattr(db, "q1", lambda *a, **k: {"source_path": "x.mp4", "meta": {}})
    monkeypatch.setattr(db, "event", lambda *a, **k: None)
    monkeypatch.setattr(media, "run", lambda *a, **k: None)
    monkeypatch.setattr(analysis, "vocabulary", lambda *a, **k: [])

    def slow_transcribe(*_a, **_k):
        time.sleep(0.05)  # long enough for the other threads to arrive: the window in which the old code collided
        return {"words": [{"w": "hello", "s": 1.0, "e": 1.4}], "segments": [], "language": "en"}

    monkeypatch.setattr(analysis, "transcribe", slow_transcribe)
    errors = []

    def run(start):
        try:
            analysis.refine_transcript("j1", start, start + 30)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=run, args=(s,)) for s in (10, 110, 210, 310, 410, 510)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    got = json.loads((jd / "analysis" / "transcript.json").read_text())  # valid JSON, not "Extra data"
    assert len(got["refined"]) == 6                                       # no thread's window was lost
    assert not list((jd / "analysis").glob(".*.tmp"))                     # no leftovers
