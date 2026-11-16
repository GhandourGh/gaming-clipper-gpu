"""Worker-owned pipeline (replaces the n8n main/assets/regen workflows).

  ingest -> analyze -> plan -> direct (per clip) -> render -> qc -> review -> finish
                       │        └ director + EDL repair (<=3), assets, music
                       └ transcript score, screen + judge ranking (parallel), select, trend hints

Every stage is a queue stage (durable in Postgres, resumable after a restart). Gemini is called directly over HTTPS;
429/5xx are retried with backoff, then rescued by OpenRouter/Groq (fallback.py). A watchdog resumes any job that is
left waiting with nothing queued, so a job cannot get stuck between stages."""
import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import config, db, fallback, gemini
from .queue import AFTER_DONE, AFTER_FAIL, Unrecoverable, enqueue, stage

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LLM_PARALLEL = int(os.environ.get("LLM_PARALLEL", "4"))  # concurrent Gemini calls per ranking pass
_llm_gate = threading.BoundedSemaphore(max(1, LLM_PARALLEL))
TIMEOUTS = {"textscore": 180, "rank": 180, "director": 240, "review": 180, "moderation": 180}
STUCK_AFTER_S = 90  # a job waiting this long with nothing queued is resumed by the watchdog


# ---------------- LLM ----------------

def gemini_call(model: str, body: dict, timeout: float = 180) -> dict:
    """POST to Gemini with retry on 429/5xx/network errors. Returns Gemini JSON or {"error": {...}} (never raises)."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"error": {"code": 0, "message": "GEMINI_API_KEY is not set on the worker"}}
    err = None
    for attempt in range(4):
        try:
            with _llm_gate:
                r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                               headers={"x-goog-api-key": key, "content-type": "application/json"})
            if r.status_code == 200:
                return r.json()
            err = {"code": r.status_code, "message": r.text[:600]}
            if r.status_code not in (408, 429, 500, 502, 503, 504):
                break
        except httpx.HTTPError as e:
            err = {"code": 0, "message": f"{type(e).__name__}: {e}"[:600]}
        time.sleep(min(40, 5 * 2 ** attempt))
    return {"error": err or {"message": "Gemini: no answer"}}


def llm(job_id: str, kind: str, req: dict, build) -> dict:
    """One model call for a request built by gemini.py/textscore.py; failures go through the fallback chain."""
    resp = gemini_call(req.get("model") or config.MAIN_MODEL, req["body"], TIMEOUTS.get(kind, 180))
    return fallback.rescue(job_id, kind, resp, build)


# ---------------- stages ----------------

def _job(job_id):
    j = db.q1("select * from jobs where id=%s", (job_id,))
    if not j:
        raise Unrecoverable("job was deleted")
    return j


def trend_hints(job_id: str) -> dict:
    from . import trends
    from .manage import load_settings
    j = _job(job_id)
    s = load_settings()
    meta = dict(j["meta"] or {})
    info = config.job_dir(str(job_id)) / "source.info.json"
    yt_title = json.loads(info.read_text()).get("title") if info.exists() else None
    game = meta.get("game") or trends.game_of(j["title"], yt_title)
    h = trends.hints(str(job_id), game, s.trends_provider) if s.trends_enabled else {"skipped": "trend hints are off in Settings"}
    meta["trends"] = h
    db.set_job(str(job_id), meta=meta)
    return h


def _rank_one(job_id: str, r: dict) -> bool:
    pass_ = r.get("pass") or "screen"
    resp = llm(job_id, "rank", r, lambda: gemini.rank_request(job_id, r["idx"], pass_))
    try:
        gemini.save_rank(job_id, r["idx"], resp, pass_)
        return True
    except Exception as e:  # noqa: BLE001 - one unparseable answer must not stop the other candidates
        db.event(job_id, "warn", "rank", f"candidate {r['idx']} ({pass_}) unusable: {str(e)[:200]}", {})
        return False


@stage("plan")
def plan(job_id, _idx=-1):
    """Transcript scoring, two-pass ranking, selection and trend hints; then one `direct` stage per chosen clip."""
    from . import textscore
    j = _job(job_id)
    if j["status"] in ("failed", "done", "no_highlights", "expired"):
        return {"skipped": j["status"]}
    if not (os.environ.get("GEMINI_API_KEY") or any(fallback.keys().values())):
        raise Unrecoverable("no AI key on the worker: add GEMINI_API_KEY (dashboard GPU page → Keys on the Pod), then Retry")
    t0 = time.time()
    if j["status"] in ("analyzed", "ranking"):
        req = textscore.request(job_id)
        if req.get("body") and not req.get("skip"):
            textscore.save(job_id, llm(job_id, "textscore", req, lambda: textscore.request(job_id)))
        db.set_job(job_id, status="ranking")
        fails: dict = {}  # (idx, pass) -> unusable answers; a candidate gets at most 2 tries per pass
        for _round in range(6):  # screen pass, judge pass, plus one retry round for unparseable answers
            todo = [r for r in gemini.rank_requests(job_id) if "idx" in r and fails.get((r["idx"], r.get("pass")), 0) < 2]
            if not todo:
                break
            with ThreadPoolExecutor(max_workers=max(1, LLM_PARALLEL)) as ex:
                oks = list(ex.map(lambda r: _rank_one(job_id, r), todo))
            for r, ok in zip(todo, oks):
                if not ok:
                    fails[(r["idx"], r.get("pass"))] = fails.get((r["idx"], r.get("pass")), 0) + 1
        chosen = gemini.select(job_id)
    else:  # resumed after selection (e.g. an old n8n job): just continue with the clips that exist
        chosen = db.q("select idx from clips where job_id=%s and status='selected' order by idx", (job_id,))
    rank_s = round(time.time() - t0, 1)
    j = _job(job_id)
    if j["status"] == "failed" or not chosen:
        enqueue(job_id, "finish", force=True)
        return {"clips": 0, "rank_seconds": rank_s}
    try:
        trend_hints(job_id)
    except Exception as e:  # noqa: BLE001 - trend hints are optional
        db.event(job_id, "warn", "trends", f"trend hints skipped: {str(e)[:200]}", {})
    for c in chosen:
        enqueue(job_id, "direct", c["idx"])
    return {"clips": len(chosen), "rank_seconds": rank_s}


@stage("direct")
def direct(job_id, idx):
    """Director plan (repaired up to 3 times), licensed assets and music; then the render stage."""
    from .assets import add_music, resolve
    res = {}
    for _attempt in range(4):
        req = gemini.director_request(job_id, idx)
        res = gemini.save_edl(job_id, idx, llm(job_id, "director", req, lambda: gemini.director_request(job_id, idx)))
        if res.get("valid") or not res.get("retry"):
            break
    clip = db.q1("select status from clips where job_id=%s and idx=%s", (job_id, idx))
    if not res.get("valid"):
        if clip and clip["status"] == "ready_for_review":  # a failed redo kept the first good version
            enqueue(job_id, "finish", force=True)
            return {"valid": False, "kept_previous": True}
        db.q("update clips set status='failed', fail_kind='plan_failed' where job_id=%s and idx=%s", (job_id, idx))
        enqueue(job_id, "finish", force=True)
        return {"valid": False, "errors": (res.get("errors") or [])[:5]}
    resolve(job_id, idx)
    try:
        add_music(job_id, idx)
    except ValueError as e:
        db.event(job_id, "warn", "music", f"clip {idx}: no music ({str(e)[:160]})", {})
    enqueue(job_id, "render", idx, force=True)
    return {"valid": True, "warnings": (res.get("warnings") or [])[:5]}


@stage("review")
def review(job_id, idx):
    """Optional moderation, then the AI review; one bounded redo goes back to `direct`."""
    out = {}
    mreq = gemini.moderation_request(job_id, idx)
    if mreq.get("body") and not mreq.get("skip"):
        out["moderation"] = gemini.save_moderation(job_id, idx, llm(job_id, "moderation", mreq, lambda: gemini.moderation_request(job_id, idx)))
    rreq = gemini.review_request(job_id, idx)
    if rreq.get("body") and not rreq.get("skip"):
        r = gemini.save_review(job_id, idx, llm(job_id, "review", rreq, lambda: gemini.review_request(job_id, idx)))
        out["review"] = {k: r.get(k) for k in ("score", "verdict", "redo")}
        if r.get("redo"):
            enqueue(job_id, "direct", idx, force=True)
            return out
    enqueue(job_id, "finish", force=True)
    return out


def _clips_pending(job_id) -> bool:
    return bool(db.q1("select 1 from stages where job_id=%s and clip_idx>=0 and status in ('queued','running') limit 1", (job_id,)))


def finish_job(job_id: str) -> dict:
    j = _job(job_id)
    clips = db.q("select status from clips where job_id=%s", (job_id,))
    ok = sum(c["status"] == "ready_for_review" for c in clips)
    if j["status"] == "failed" and not ok:
        status = "failed"
    else:
        status = "done" if ok else ("no_highlights" if not clips else "failed")
    meta = dict(j["meta"] or {})
    meta["finished_at"] = time.time()
    err = None if ok else (j["error"] or _first_error(job_id) or "no clip passed QC")
    db.set_job(job_id, status=status, meta=meta, error=err)
    for p in config.job_dir(str(job_id)).glob("analysis/cand_*.mp4"):  # bulky intermediates; the source stays
        p.unlink(missing_ok=True)
    return {"status": status, "ok": ok, "clips": len(clips)}


def _first_error(job_id) -> str | None:
    """The root cause of a failed job: the first failed stage, so a render failure is not reported as a QC failure."""
    r = db.q1("select name, clip_idx, error from stages where job_id=%s and status='failed' order by finished_at nulls last limit 1", (job_id,))
    if not r:
        return None
    kind = {"render": "render_failed", "qc": "qc_failed", "direct": "plan_failed", "ingest": "ingest_failed",
            "analyze": "analysis_failed", "plan": "ai_unavailable", "review": "review_failed"}.get(r["name"], f"{r['name']}_failed")
    where = f" (clip {r['clip_idx']})" if r["clip_idx"] >= 0 else ""
    return f"{kind}{where}: {(r['error'] or '')[:400]}"


@stage("finish")
def finish(job_id, _idx=-1):
    if _clips_pending(job_id):
        return {"waiting": True}  # another clip is still working; its own finish call completes the job
    j = _job(job_id)
    if j["status"] in ("created", "ingested", "downloading", "analyzing", "analyzed", "ranking"):
        return {"waiting": True, "status": j["status"]}
    res = finish_job(job_id)
    meta = dict(_job(job_id)["meta"] or {})
    run = str(meta.get("t0"))
    if meta.get("notified_run") != run:
        try:
            from . import notify
            notify.job_finished(job_id)
        except Exception as e:  # noqa: BLE001 - notifications are best effort
            db.event(job_id, "warn", "notify", f"notification failed: {str(e)[:200]}", {})
        meta["notified_run"] = run
        db.set_job(job_id, meta=meta)
    return res


# ---------------- chaining ----------------

def _after_analyze(job_id, _idx, _res):
    if (_job(job_id)["status"]) in ("analyzed", "ranking"):
        enqueue(job_id, "plan", force=True)


def _after_qc(job_id, idx, _res):
    enqueue(job_id, "review", idx, force=True)


def _after_fail(job_id, name, idx, _err):
    if idx >= 0 or name in ("plan",):
        enqueue(job_id, "finish", force=True)


AFTER_DONE.update({"analyze": _after_analyze, "qc": _after_qc})
AFTER_FAIL.append(_after_fail)


# ---------------- watchdog ----------------

def resume(job_id: str) -> str:
    """Push a job forward from wherever it stopped (resubmit, restart, watchdog). Returns what was queued."""
    j = _job(job_id)
    st = j["status"]
    if st in ("created", "ingested") and not db.q1("select 1 from stages where job_id=%s and name='ingest' and status='done'", (job_id,)):
        enqueue(job_id, "ingest")
        return "ingest"
    if st == "ingested":
        enqueue(job_id, "analyze")
        return "analyze"
    if st in ("analyzed", "ranking"):
        enqueue(job_id, "plan", force=True)
        return "plan"
    if st == "selected":
        queued = []
        for c in db.q("select idx, status, edl from clips where job_id=%s", (job_id,)):
            if c["status"] == "selected" or (c["status"] in ("planned", "assets_ready") and not c["edl"]):
                enqueue(job_id, "direct", c["idx"], force=True)
                queued.append(f"direct:{c['idx']}")
            elif c["status"] == "planned":
                enqueue(job_id, "direct", c["idx"], force=True)  # assets + music are re-resolved, then render
                queued.append(f"direct:{c['idx']}")
            elif c["status"] == "assets_ready":
                enqueue(job_id, "render", c["idx"], force=True)
                queued.append(f"render:{c['idx']}")
            elif c["status"] == "rendered":
                enqueue(job_id, "qc", c["idx"], force=True)
                queued.append(f"qc:{c['idx']}")
        if not queued:
            enqueue(job_id, "finish", force=True)
            queued.append("finish")
        return ",".join(queued)
    return "nothing"


def resume_stalled() -> list[str]:
    """Jobs that wait between stages with nothing queued (crash, old n8n job, lost hook) are pushed forward."""
    moved = []
    rows = db.q("""select j.id, j.status from jobs j
                   where j.status in ('analyzed','ranking','selected','ingested')
                     and j.updated_at < now() - make_interval(secs => %s)
                     and not exists (select 1 from stages s where s.job_id=j.id and s.status in ('queued','running'))""",
                (STUCK_AFTER_S,))
    for r in rows:
        jid = str(r["id"])
        what = resume(jid)
        db.event(jid, "warn", "watchdog", f"job was waiting in '{r['status']}' with nothing queued; resumed ({what})", {})
        moved.append(jid)
    return moved


def _watchdog():
    while True:
        time.sleep(30)
        try:
            resume_stalled()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def start():
    threading.Thread(target=_watchdog, daemon=True, name="clipper-watchdog").start()
