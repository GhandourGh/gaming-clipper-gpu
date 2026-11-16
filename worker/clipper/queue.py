/Users/macos/.zshenv:.:1: no such file or directory: /Users/macos/.cargo/env
"""Background job engine. Stages are idempotent and resumable: a finished stage is never re-run unless force=True,
and a restart re-queues whatever was queued/running.

Stages run in lanes so a slow download or a slow LLM call never blocks the GPU:
  ingest -> downloads/copies (INGEST_THREADS threads)
  analysis -> analyze (1 thread; the shared Whisper model is intentionally serialized)
  gpu    -> render (GPU_THREADS threads; clips may run concurrently)
  cpu    -> qc (QC_THREADS threads; QC must not occupy an NVENC/Whisper slot)
  llm    -> plan, direct, review, finish (LLM_THREADS threads; network-bound)
Hooks (AFTER_DONE / AFTER_FAIL) chain stages, so the pipeline never depends on an outside orchestrator."""
import os
import queue as _q
import threading
import time
import traceback
from contextlib import contextmanager

from . import db

HANDLERS = {}  # stage name -> fn(job_id, clip_idx) -> result dict
LANE_OF = {
    "ingest": "ingest",
    "analyze": "analysis",
    "qc": "cpu",
    "plan": "llm",
    "direct": "llm",
    "review": "llm",
    "finish": "llm",
}  # default lane: gpu


def _threads(name: str, default: int) -> int:
    """Read a worker limit without allowing a bad env value to disable a lane."""
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


LANE_THREADS = {
    "ingest": _threads("INGEST_THREADS", 2),
    "analysis": 1,
    "gpu": _threads("GPU_THREADS", 2),
    "cpu": _threads("QC_THREADS", 2),
    "llm": _threads("LLM_THREADS", 3),
}
_QS = {lane: _q.Queue() for lane in LANE_THREADS}


class _WeightedCapacity:
    """Atomic weighted semaphore; avoids partial-acquire deadlocks."""
    def __init__(self, total: int):
        self.total = total
        self.available = total
        self.changed = threading.Condition()

    def acquire(self, count: int):
        with self.changed:
            self.changed.wait_for(lambda: self.available >= count)
            self.available -= count

    def release(self, count: int):
        with self.changed:
            self.available = min(self.total, self.available + count)
            self.changed.notify_all()


_GPU_SLOTS = _WeightedCapacity(LANE_THREADS["gpu"])
MAX_ATTEMPTS = 3
AFTER_DONE: dict = {}  # stage name -> fn(job_id, clip_idx, result)
AFTER_FAIL: list = []  # fn(job_id, stage_name, clip_idx, error)
_started = False


class Unrecoverable(Exception):
    """Raise for errors that retrying cannot fix (bad input, no rights, ...)."""


@contextmanager
def _gpu_capacity(name: str):
    """Render uses one slot; Whisper analysis temporarily owns the whole GPU.

    This gives real parallel NVENC renders without letting a large Whisper model
    overlap them and exhaust VRAM. Non-GPU stages pass through immediately.
    """
    count = LANE_THREADS["gpu"] if name == "analyze" else (1 if name == "render" else 0)
    _GPU_SLOTS.acquire(count)
    try:
        yield
    finally:
        _GPU_SLOTS.release(count)


def stage(name):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def lane(name: str) -> str:
    return LANE_OF.get(name, "gpu")


def qsize() -> int:
    return sum(q.qsize() for q in _QS.values())


def stats() -> dict:
    """Small, secret-free queue snapshot for the dashboard and operations checks."""
    running = db.q("select name, count(*)::int as n from stages where status='running' group by name")
    queued = db.q("select name, count(*)::int as n from stages where status='queued' group by name")
    return {
        "workers": dict(LANE_THREADS),
        "running": {r["name"]: r["n"] for r in running},
        "queued": {r["name"]: r["n"] for r in queued},
        "in_memory": {name: q.qsize() for name, q in _QS.items()},
    }


def get_stage(job_id, name, clip_idx=-1):
    return db.q1("select * from stages where job_id=%s and name=%s and clip_idx=%s", (job_id, name, clip_idx))


def _put(job_id, name, clip_idx, not_before=0.0):
    _QS[lane(name)].put((str(job_id), name, clip_idx, not_before))


def enqueue(job_id, name, clip_idx=-1, force=False):
    cur = get_stage(job_id, name, clip_idx)
    if cur and not force and cur["status"] in ("queued", "running", "done"):
        return cur  # idempotent
    if cur and force and cur["status"] in ("queued", "running"):
        return cur  # already pending: never run the same stage twice at once
    db.q("""insert into stages(job_id,name,clip_idx,status,attempts) values (%s,%s,%s,'queued',0)
            on conflict (job_id,name,clip_idx) do update
            set status='queued', attempts=0, error=null, result=null, started_at=null, finished_at=null""",
         (job_id, name, clip_idx))
    _put(job_id, name, clip_idx)
    return get_stage(job_id, name, clip_idx)


def _claim(job_id, name, clip_idx):
    """Atomically move a queued stage to running; None when it was deleted, cancelled or already claimed."""
    if db.q1("select 1 from jobs where id=%s and status='cancelled'", (job_id,)):
        db.q("update stages set status='cancelled', finished_at=now() where job_id=%s and name=%s and clip_idx=%s and status='queued'",
             (job_id, name, clip_idx))
        return None
    return db.q1("update stages set status='running', attempts=attempts+1, started_at=now(), error=null "
                 "where job_id=%s and name=%s and clip_idx=%s and status='queued' returning *", (job_id, name, clip_idx))


def _run_one(job_id, name, clip_idx):
    st = _claim(job_id, name, clip_idx)
    if not st:
        return
    t0 = time.time()
    try:
        with _gpu_capacity(name):
            result = HANDLERS[name](job_id, clip_idx) or {}
        result["seconds"] = round(time.time() - t0, 2)
        db.q("update stages set status='done', finished_at=now(), result=%s "
             "where job_id=%s and name=%s and clip_idx=%s", (db.J(result), job_id, name, clip_idx))
        db.event(job_id, "info", name, "stage done", {"clip": clip_idx, "seconds": result["seconds"]})
        hook = AFTER_DONE.get(name)
        if hook:
            try:
                hook(job_id, clip_idx, result)
            except Exception:  # noqa: BLE001 - a hook error must not undo a finished stage
                db.event(job_id, "error", name, "after-stage hook failed", {"trace": traceback.format_exc()[-1500:]})
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        retry = (not isinstance(e, Unrecoverable) and st["attempts"] < MAX_ATTEMPTS
                 and not str(e).startswith("render_failed"))  # a deterministic render failure does not get better
        db.event(job_id, "error", name, msg, {"clip": clip_idx, "attempt": st["attempts"], "retry": retry,
                                              "trace": traceback.format_exc()[-2000:]})
        if retry:
            db.q("update stages set status='queued', error=%s where job_id=%s and name=%s and clip_idx=%s",
                 (msg, job_id, name, clip_idx))
            _put(job_id, name, clip_idx, time.time() + 5 * 3 ** (st["attempts"] - 1))  # 5s, 15s backoff
            return
        db.q("update stages set status='failed', finished_at=now(), error=%s "
             "where job_id=%s and name=%s and clip_idx=%s", (msg, job_id, name, clip_idx))
        if clip_idx < 0:
            db.set_job(job_id, status="failed", error=f"{name}: {msg}")
        else:
            db.q("update clips set status='failed', fail_kind=%s where job_id=%s and idx=%s", (f"{name}_failed", job_id, clip_idx))
        for hook in AFTER_FAIL:
            try:
                hook(job_id, name, clip_idx, msg)
            except Exception:  # noqa: BLE001
                db.event(job_id, "error", name, "failure hook failed", {"trace": traceback.format_exc()[-1500:]})


def _loop(lane_name):
    q = _QS[lane_name]
    while True:
        job_id, name, clip_idx, not_before = q.get()
        if not_before > time.time():
            time.sleep(min(1.0, not_before - time.time()))
            q.put((job_id, name, clip_idx, not_before))
            continue
        try:
            _run_one(job_id, name, clip_idx)
        except Exception:  # noqa: BLE001  one bad item (e.g. a job deleted mid-run) must never kill a lane
            traceback.print_exc()


def busy() -> bool:
    return qsize() > 0 or bool(db.q1("select 1 from stages where status in ('queued','running') limit 1"))


def start():
    global _started
    if _started:
        return
    _started = True
    # resume anything interrupted by a restart (a 'running' stage died with the old process)
    db.q("update stages set status='queued' where status='running'")
    for r in db.q("select job_id,name,clip_idx from stages where status='queued' order by started_at nulls last"):
        _put(r["job_id"], r["name"], r["clip_idx"])
    for lane_name, n in LANE_THREADS.items():
        for i in range(max(1, n)):
            threading.Thread(target=_loop, args=(lane_name,), daemon=True, name=f"clipper-{lane_name}-{i}").start()
