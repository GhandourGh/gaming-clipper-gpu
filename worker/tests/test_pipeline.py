"""Worker-owned pipeline: Gemini retry behaviour and lane routing (no network, no database)."""
import threading
import time

from clipper import media, pipeline, queue


class _R:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body or {}, text

    def json(self):
        return self._body


def test_gemini_call_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = pipeline.gemini_call("m", {})
    assert "GEMINI_API_KEY" in out["error"]["message"]


def test_gemini_call_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []
    answers = [_R(429, text="quota"), _R(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})]
    monkeypatch.setattr(pipeline.httpx, "post", lambda *a, **kw: calls.append(kw["headers"]) or answers.pop(0))
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    out = pipeline.gemini_call("m", {"contents": []})
    assert out["candidates"] and len(calls) == 2
    assert calls[0]["x-goog-api-key"] == "k"  # key in a header, never in the URL


def test_gemini_call_does_not_retry_bad_request(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []
    monkeypatch.setattr(pipeline.httpx, "post", lambda *a, **kw: calls.append(1) or _R(400, text="bad schema"))
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    out = pipeline.gemini_call("m", {})
    assert out["error"]["code"] == 400 and len(calls) == 1


def test_stage_lanes():
    assert queue.lane("render") == "gpu"
    assert queue.lane("analyze") == "analysis"
    assert queue.lane("qc") == "cpu"
    assert queue.lane("ingest") == "ingest"
    assert {queue.lane(s) for s in ("plan", "direct", "review", "finish")} == {"llm"}
    assert {"plan", "direct", "review", "finish"} <= set(queue.HANDLERS)


def test_parallel_clip_worker_defaults():
    assert queue.LANE_THREADS["gpu"] >= 2
    assert queue.LANE_THREADS["analysis"] == 1
    assert queue.LANE_THREADS["cpu"] >= 2
    assert queue.LANE_THREADS["ingest"] >= 2


def test_two_renders_can_hold_gpu_capacity_together():
    entered = []
    release = threading.Event()

    def hold():
        with queue._gpu_capacity("render"):
            entered.append(1)
            release.wait(1)

    threads = [threading.Thread(target=hold) for _ in range(2)]
    for thread in threads:
        thread.start()
    deadline = time.time() + 1
    while len(entered) < 2 and time.time() < deadline:
        time.sleep(.01)
    release.set()
    for thread in threads:
        thread.join(1)
    assert len(entered) == 2


def test_h264_args_match_host():
    args = media.h264("proxy")
    assert args[1] == ("h264_nvenc" if media.NVENC else "libx264")
