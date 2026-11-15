"""Single-worker background queue. Stages are idempotent and resumable:
a finished stage is never re-run unless force=True, and a crash re-queues
whatever was queued/running at startup."""
import queue as _q
import threading
import time
import traceback

from . import db

_Q: _q.Queue = _q.Queue()
HANDLERS = {}  # stage name -> fn(job_id, clip_idx) -> result dict
MAX_ATTEMPTS = 3


class Unrecoverable(Exception):
    """Raise for errors that retrying cannot fix (bad input, no rights, ...)."""


def stage(name):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def get_stage(job_id, name, clip_idx=-1):
    return db.q1("select * from stages where job_id=%s and name=%s and clip_idx=%s", (job_id, name, clip_idx))


def enqueue(job_id, name, clip_idx=-1, force=False):
    cur = get_stage(job_id, name, clip_idx)
    if cur and not force and cur["status"] in ("queued", "running", "done"):
        return cur  # idempotent
    db.q("""insert into stages(job_id,name,clip_idx,status,attempts) values (%s,%s,%s,'queued',0)
            on conflict (job_id,name,clip_idx) do update
            set status='queued', attempts=0, error=null, result=null, started_at=null, finished_at=null""",
         (job_id, name, clip_idx))
    _Q.put((str(job_id), name, clip_idx, 0.0))
    return get_stage(job_id, name, clip_idx)


def _run_one(job_id, name, clip_idx):
    if not get_stage(job_id, name, clip_idx):  # the job was deleted while this was waiting in the queue
        return
    db.q("update stages set status='running', attempts=attempts+1, started_at=now(), error=null "
         "where job_id=%s and name=%s and clip_idx=%s", (job_id, name, clip_idx))
    st = get_stage(job_id, name, clip_idx)
    t0 = time.time()
    try:
        result = HANDLERS[name](job_id, clip_idx) or {}
        result["seconds"] = round(time.time() - t0, 2)
        db.q("update stages set status='done', finished_at=now(), result=%s "
             "where job_id=%s and name=%s and clip_idx=%s", (db.J(result), job_id, name, clip_idx))
        db.event(job_id, "info", name, "stage done", {"clip": clip_idx, "seconds": result["seconds"]})
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        retry = not isinstance(e, Unrecoverable) and st["attempts"] < MAX_ATTEMPTS
        db.event(job_id, "error", name, msg, {"clip": clip_idx, "attempt": st["attempts"], "retry": retry,
                                              "trace": traceback.format_exc()[-2000:]})
        if retry:
            db.q("update stages set status='queued', error=%s where job_id=%s and name=%s and clip_idx=%s",
                 (msg, job_id, name, clip_idx))
            _Q.put((job_id, name, clip_idx, time.time() + 5 * 3 ** (st["attempts"] - 1)))  # 5s, 15s backoff
        else:
            db.q("update stages set status='failed', finished_at=now(), error=%s "
                 "where job_id=%s and name=%s and clip_idx=%s", (msg, job_id, name, clip_idx))
            if clip_idx < 0:
                db.set_job(job_id, status="failed", error=f"{name}: {msg}")
            else:
                db.q("update clips set status='failed' where job_id=%s and idx=%s", (job_id, clip_idx))


def _loop():
    while True:
        job_id, name, clip_idx, not_before = _Q.get()
        if not_before > time.time():
            time.sleep(min(1.0, not_before - time.time()))
            _Q.put((job_id, name, clip_idx, not_before))
            continue
        try:
            _run_one(job_id, name, clip_idx)
        except Exception:  # noqa: BLE001  one bad item (e.g. a job deleted mid-run) must never kill the queue thread
            traceback.print_exc()


def start():
    # resume anything interrupted by a restart
    for r in db.q("select job_id,name,clip_idx from stages where status in ('queued','running') order by started_at nulls last"):
        _Q.put((str(r["job_id"]), r["name"], r["clip_idx"], 0.0))
    threading.Thread(target=_loop, daemon=True, name="clipper-queue").start()
