"""clipper-worker HTTP API. Binds to 127.0.0.1 only; every route except /health and /files requires the
X-Clipper-Token header. The worker owns the whole pipeline (queue.py + pipeline.py); nothing outside drives it."""
import hashlib
import html
import json
import shutil
import subprocess
import time
import uuid
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import analysis, assets, config, db, gemini, ingest, pipeline, qc, queue, render  # noqa: F401 (registers stages)

app = FastAPI(title="clipper-worker", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)


def auth(x_clipper_token: str = Header("")):
    if not config.TOKEN or not hashlib.sha256(x_clipper_token.encode()).digest() == hashlib.sha256(config.TOKEN.encode()).digest():
        raise HTTPException(401, "bad token")


A = [Depends(auth)]


@app.on_event("startup")
def _startup():
    db.pool()
    queue.start()
    pipeline.start()
    from . import ops
    ops.start()
    from . import live
    live.start(_live_submit)


@app.get("/health")
def health():
    db.q1("select 1 as ok")
    return {"ok": True, "queue": queue.qsize()}


class JobIn(BaseModel):
    source_type: str = Field(pattern="^(youtube|twitch|file)$")
    url: str | None = None
    filename: str | None = None
    rights_confirmed: bool
    title: str | None = None
    style: str | None = Field(None, pattern="^(viral|funny|cinematic|fast|minimal)$")
    clips: int | None = Field(None, ge=1, le=8, description="exact number of clips wanted; tops up with the best remaining moments")
    min_seconds: int | None = Field(None, ge=8, le=150, description="target clip length (lower bound)")
    max_seconds: int | None = Field(None, ge=10, le=180, description="target clip length (upper bound)")
    game: str | None = Field(None, max_length=60, description="game name for trend hints (guessed from the title if empty)")
    fresh: bool = Field(False, description="always create a new job, even if this video was processed before")
    captions: str | None = Field(None, pattern="^(overlay|bar|off)$", description="captions over the video, in their own strip, or none")
    engine: str = Field("stories", pattern="^(stories|moments)$", description="stories links related moments across the full source; moments uses the legacy local-window clipper")
    template: str = Field("auto", pattern="^(auto|full|split_50|split_20|gta6_gameplay)$", description="layout template")


def _length(j) -> list[int] | None:
    if not (j.min_seconds or j.max_seconds):
        return None
    lo = j.min_seconds or max(8, int((j.max_seconds or 60) * 0.75))
    hi = max(lo + 5, j.max_seconds or int(lo * 1.35))
    return [lo, hi]


def _src_key(j: "JobIn") -> str | None:
    if j.source_type == "youtube":
        v = ingest.youtube_id(j.url or "")
        return f"yt:{v}" if v else None
    if j.source_type == "twitch":
        v = ingest.twitch_id(j.url or "")
        return f"twitch:{v}" if v else None
    return f"file:{j.filename}" if j.filename else None


_PENDING: dict[str, tuple[float, dict]] = {}  # source key -> (time, options): the dashboard sends these just before the job is created


@app.post("/jobs", dependencies=A)
def create_job(j: JobIn):
    out = _create_job(j)
    k = _src_key(j)
    hit = _PENDING.pop(k, None) if k else None
    if hit and time.time() - hit[0] < 600 and out.get("job_id"):
        try:  # a bad option must never fail job creation: n8n would see a 500 and drop the job
            _apply_options(out["job_id"], OptionsIn(**hit[1]))
        except Exception as e:  # noqa: BLE001
            import traceback
            db.event(out["job_id"], "warn", "create", "options could not be applied; using defaults",
                     {"error": f"{type(e).__name__}: {e}"[:300], "trace": traceback.format_exc()[-1200:]})
    return out


def _create_job(j: JobIn):
    from .manage import load_settings
    d = load_settings()  # owner defaults from the dashboard; explicit request values win
    j.style = j.style or d.style
    j.clips = j.clips or d.clips
    if not (j.min_seconds or j.max_seconds):
        j.min_seconds, j.max_seconds = d.min_seconds, d.max_seconds
    if not j.rights_confirmed:
        raise HTTPException(400, "You must confirm you own the video or have the rights to edit it.")
    if j.source_type == "youtube":
        vid = ingest.youtube_id(j.url or "")
        if not vid:
            raise HTTPException(400, "Not a valid YouTube video URL (watch, youtu.be, live or shorts link).")
        key, src_url, src_path = f"yt:{vid}", f"https://www.youtube.com/watch?v={vid}", None
    elif j.source_type == "twitch":
        vid = ingest.twitch_id(j.url or "")
        if not vid:
            raise HTTPException(400, "Not a valid finished Twitch VOD URL (expected twitch.tv/videos/123…).")
        key, src_url, src_path = f"twitch:{vid}", f"https://www.twitch.tv/videos/{vid}", None
    else:
        try:
            p = ingest.inbox_file(j.filename or "")
        except queue.Unrecoverable as e:
            raise HTTPException(400, str(e)) from e
        key, src_url, src_path = ingest.file_key(p), None, str(p)
    key = f"{key}:{j.engine}"  # the story and legacy engines produce different analysis artifacts
    if j.fresh:  # a deliberate re-upload: never reuse the old job
        key = f"{key}#{uuid.uuid4().hex[:8]}"
    ex = db.q1("select id, status from jobs where idem_key=%s", (key,))
    if ex and ex["status"] not in ("failed", "done", "no_highlights", "expired"):
        return {"job_id": str(ex["id"]), "status": ex["status"], "deduplicated": True}
    if ex:  # retry a failed job in place, resuming after the last finished stage
        jid = str(ex["id"])
        if ex["status"] == "expired":  # files were cleaned up: start over from ingest
            db.q("delete from clips where job_id=%s", (jid,))
            db.q("delete from stages where job_id=%s", (jid,))
        done = {s["name"] for s in db.q("select name from stages where job_id=%s and clip_idx=-1 and status='done'", (jid,))}
        if db.q1("select 1 from clips where job_id=%s", (jid,)):
            db.q("update clips set status='planned' where job_id=%s and status in ('failed','qc_failed') and edl is not null", (jid,))
            db.q("delete from stages where job_id=%s and clip_idx>=0 and status='failed'", (jid,))
            status = "selected"  # the pipeline re-plans / re-renders clips; analysis + ranking are reused
        elif "analyze" in done:
            status = "analyzed"
        else:
            status = "created"
            queue.enqueue(jid, "ingest" if "ingest" not in done else "analyze", force=True)
        meta = dict(db.q1("select meta from jobs where id=%s", (jid,))["meta"] or {})
        meta["t0"] = time.time()
        meta["engine"] = j.engine
        if j.style:
            meta["style"] = j.style
        if j.clips:
            meta["clips"] = j.clips
        if j.game:
            meta["game"] = j.game
        meta["template"] = j.template
        if j.min_seconds or j.max_seconds:
            meta["length"] = _length(j)
        db.set_job(jid, _force=True, status=status, error=None, meta=meta)
        db.event(jid, "info", "create", f"resubmitted; resuming at status {status}", {"style": meta.get("style")})
        if status != "created":
            pipeline.resume(jid)
        return {"job_id": jid, "status": status, "deduplicated": True}
    jid = str(uuid.uuid4())
    db.q("insert into jobs(id, idem_key, source_type, source_url, source_path, title, meta) values (%s,%s,%s,%s,%s,%s,%s)",
         (jid, key, j.source_type, src_url, src_path, j.title, db.J({"t0": time.time(), "style": j.style or "viral", "clips": j.clips, "length": _length(j), "game": j.game, "captions": j.captions, "engine": j.engine, "template": j.template})))
    db.event(jid, "info", "create", "job created", {"source_type": j.source_type, "key": key})
    queue.enqueue(jid, "ingest")
    return {"job_id": jid, "status": "created", "deduplicated": False}


class OptionsIn(BaseModel):
    mode: str | None = None
    content_type: str | None = None
    edit: str | None = None
    silence: str | None = None
    zoom: str | None = None
    sfx: str | None = None
    caption_look: str | None = None
    instructions: str | None = Field(None, max_length=600)
    clips: int | None = Field(None, ge=1, le=12)
    min_seconds: int | None = Field(None, ge=8, le=150)
    max_seconds: int | None = Field(None, ge=10, le=180)
    captions: str | None = Field(None, pattern="^(overlay|bar|off)$")
    template: str | None = Field(None, pattern="^(auto|full|split_50|split_20|gta6_gameplay)$")
    clip_strategy: str | None = Field(None, pattern="^(single|gta_launch_mix)$")
    facecam_size: str | None = Field(None, pattern="^(small|medium|large)$")
    facecam_position: str | None = Field(None, pattern="^(top_left|top_right|mid_left|mid_right)$")
    game_logo: str | None = Field(None, pattern="^(?:[a-z0-9_-]{1,40}|none)$")
    gameplay_crop: str | None = Field(None, pattern="^(auto|left|center|right|fit)$")


def _apply_options(jid: str, o: "OptionsIn") -> dict:
    from . import modes
    meta = dict(_job(jid)["meta"] or {})
    n = modes.normalize(o.model_dump(exclude_none=True))
    meta.pop("instructions", None)
    meta.update(n)
    meta["style"] = modes.style_for(n["mode"], n["edit"])
    meta["engine"] = "stories"
    if o.clips:
        meta["clips"] = o.clips
    if o.min_seconds or o.max_seconds:
        meta["length"] = _length(o)
    for k in ("captions", "template", "clip_strategy", "facecam_size", "facecam_position", "game_logo", "gameplay_crop"):
        if getattr(o, k):
            meta[k] = getattr(o, k)
    if meta.get("template") != "gta6_gameplay":
        meta["clip_strategy"] = "single"
    elif meta.get("clip_strategy") == "gta_launch_mix":
        meta.pop("length", None)  # each selected clip gets its own short or story target
    db.set_job(jid, meta=meta)
    db.event(jid, "info", "create", f"options: {n['mode']} mode, {n['edit']} editing", {k: meta.get(k) for k in ("mode", "style", "length", "clips", "silence", "zoom", "sfx", "captions", "template")})
    return {"ok": True, "mode": n["mode"], "style": meta["style"], "length": meta.get("length"), "clips": meta.get("clips")}


class PendingIn(BaseModel):
    source: str = Field(max_length=500, description="the video URL or the inbox file name the job is about to be created for")
    options: dict


@app.post("/jobs/pending-options", dependencies=A)
def pending_options(p: PendingIn):
    """The dashboard sends the mode/options first; the job (created through n8n a moment later) picks them up."""
    probe = JobIn(source_type="youtube" if "youtu" in p.source else "twitch" if "twitch.tv" in p.source else "file",
                  url=p.source if p.source.startswith("http") else None, filename=None if p.source.startswith("http") else p.source,
                  rights_confirmed=True)
    k = _src_key(probe)
    if not k:
        raise HTTPException(400, "unrecognised video source")
    OptionsIn(**p.options)  # validate now
    _PENDING[k] = (time.time(), p.options)
    return {"ok": True}


@app.post("/jobs/{job_id}/options", dependencies=A)
def set_job_options(job_id: uuid.UUID, o: "OptionsIn"):
    """Change the mode / editing options of an existing job (later stages and regenerated clips use them)."""
    return _apply_options(str(job_id), o)


@app.post("/upload", dependencies=A)
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "upload.mp4").name
    if Path(name).suffix.lower() not in ingest.VIDEO_EXT:
        raise HTTPException(400, "unsupported file type")
    dst = config.INBOX / name
    # write to a temp file and rename: a broken upload never leaves a half file, and an existing file with the
    # same name is replaced (new inode), not overwritten in place (job folders hard-link inbox files)
    tmp = config.INBOX / f".{name}.{uuid.uuid4().hex[:8]}.part"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f, 8 << 20)
        if tmp.stat().st_size == 0:
            raise HTTPException(400, "the uploaded file is empty")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return {"filename": name, "bytes": dst.stat().st_size}


@app.post("/upload-logo", dependencies=A)
async def upload_logo(file: UploadFile = File(...)):
    """Store a user-supplied template logo as a safe, renderer-ready PNG."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".png", ".webp", ".jpg", ".jpeg"):
        raise HTTPException(400, "Choose a PNG, WebP, or JPG logo.")
    raw = await file.read(8 * 1024 * 1024 + 1)
    if not raw:
        raise HTTPException(400, "the uploaded logo is empty")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "logo must be smaller than 8 MB")
    slug = f"custom-{uuid.uuid4().hex[:12]}"
    logo_dir = config.RENDERER / "public" / "logos"
    logo_dir.mkdir(parents=True, exist_ok=True)
    tmp = logo_dir / f".{slug}{suffix}"
    dst = logo_dir / f"{slug}.png"
    tmp.write_bytes(raw)
    try:
        result = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(tmp),
                                 "-vf", "scale='min(1200,iw)':-2:flags=lanczos,format=rgba", "-frames:v", "1", str(dst)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode or not dst.exists():
            raise HTTPException(400, "Could not read that image. Try a different PNG or WebP file.")
    finally:
        tmp.unlink(missing_ok=True)
    return {"slug": slug, "filename": dst.name}


def _job(job_id):
    j = db.q1("select * from jobs where id=%s", (job_id,))
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/jobs/{job_id}", dependencies=A)
def job_status(job_id: uuid.UUID):
    j = _job(job_id)
    stages = db.q("select name, clip_idx, status, attempts, error, result, started_at, finished_at from stages where job_id=%s order by clip_idx, started_at nulls last", (job_id,))
    clips = db.q("select idx, status, review, qc, output_path, candidate, edl, ai_review, render_engine, encoder, render_seconds, "
                 "render_note, fail_kind from clips where job_id=%s order by idx", (job_id,))
    cost = db.q1("select coalesce(sum(usd),0) as usd from costs where job_id=%s", (job_id,))["usd"]
    t0 = (j["meta"] or {}).get("t0")
    pending = [s for s in stages if s["status"] in ("queued", "running")]
    failed = [s for s in stages if s["status"] == "failed"]
    out = []
    for c in clips:
        cand, edl = c["candidate"], c["edl"] or {}
        out.append({"idx": c["idx"], "status": c["status"], "review": c["review"], "title": edl.get("title") or cand.get("title"),
                    "description": edl.get("description") or cand.get("why_engaging"), "event": cand.get("event"),
                    "source_start": cand["start"], "source_end": cand["end"], "quality": cand["quality"], "confidence": cand["confidence"],
                    "qc_passed": (c["qc"] or {}).get("passed"), "qc_fails": (c["qc"] or {}).get("fails"),
                    "file": f"{config.PUBLIC_BASE}/files/{job_id}/{Path(c['output_path']).name}" if c["output_path"] else None,
                    "local_path": c["output_path"], "content_type": cand.get("content_type"),
                    "below_quality_bar": bool(cand.get("below_quality_bar")),
                    "ai_review_score": (c["ai_review"] or {}).get("score"), "ai_review_verdict": (c["ai_review"] or {}).get("verdict"),
                    "render_engine": c["render_engine"], "encoder": c["encoder"], "render_seconds": c["render_seconds"],
                    "render_note": c["render_note"], "fail_kind": c["fail_kind"],
                    "cpu_fallback": bool(c["render_note"] and c["render_note"].startswith("CPU fallback"))})
    return {"job_id": str(job_id), "status": j["status"], "error": j["error"], "title": j["title"],
            "source": (j["meta"] or {}).get("source"), "burned_captions": ((j["meta"] or {}).get("burned_captions") or {}).get("present"), "busy": bool(pending), "pending": [f"{s['name']}:{s['clip_idx']}" for s in pending],
            "failed": [{"stage": s["name"], "clip": s["clip_idx"], "error": s["error"]} for s in failed],
            "elapsed_s": round(time.time() - t0, 1) if t0 else None, "cost_usd": float(cost), "clips": out,
            "stages": [{"name": s["name"], "clip": s["clip_idx"], "status": s["status"], "attempts": s["attempts"],
                        "seconds": (s["result"] or {}).get("seconds"), "error": s["error"]} for s in stages],
            "root_cause": pipeline._first_error(str(job_id)) if failed else None,
            "view": f"{config.PUBLIC_BASE}/jobs/{job_id}/view"}


@app.get("/jobs/{job_id}/rank-requests", dependencies=A)
def rank_requests(job_id: uuid.UUID):
    j = _job(job_id)
    if j["status"] not in ("analyzed", "ranking"):
        raise HTTPException(409, f"job is {j['status']}, not analyzed")
    db.set_job(job_id, status="ranking")
    return gemini.rank_requests(str(job_id))


@app.post("/jobs/{job_id}/rank/{idx}", dependencies=A)
def rank_result(job_id: uuid.UUID, idx: int, resp: dict, pass_: str = Query("screen", alias="pass", pattern="^(screen|judge)$")):
    try:
        return gemini.save_rank(str(job_id), idx, resp, pass_)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        db.event(str(job_id), "warn", "rank", f"candidate {idx} unparseable", {"error": str(e)[:300]})
        raise HTTPException(422, str(e)[:500]) from e


@app.post("/jobs/{job_id}/select", dependencies=A)
def select(job_id: uuid.UUID):
    _job(job_id)
    chosen = gemini.select(str(job_id))
    return {"selected": [{"idx": c["idx"], "title": c["title"], "quality": c["quality"], "start": c["start"], "end": c["end"]} for c in chosen]}


def _clip(job_id, idx):
    c = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not c:
        raise HTTPException(404, "clip not found")
    return c


@app.get("/jobs/{job_id}/clips/{idx}/director-request", dependencies=A)
def director_request(job_id: uuid.UUID, idx: int):
    _clip(job_id, idx)
    return gemini.director_request(str(job_id), idx)


@app.post("/jobs/{job_id}/clips/{idx}/edl", dependencies=A)
def edl_result(job_id: uuid.UUID, idx: int, resp: dict):
    _clip(job_id, idx)
    return gemini.save_edl(str(job_id), idx, resp)


@app.get("/jobs/{job_id}/clips/{idx}/review-request", dependencies=A)
def review_request(job_id: uuid.UUID, idx: int):
    _clip(job_id, idx)
    return gemini.review_request(str(job_id), idx)


@app.post("/jobs/{job_id}/clips/{idx}/ai-review", dependencies=A)
def ai_review(job_id: uuid.UUID, idx: int, resp: dict):
    _clip(job_id, idx)
    return gemini.save_review(str(job_id), idx, resp)


@app.get("/jobs/{job_id}/clips/{idx}/moderation-request", dependencies=A)
def moderation_request(job_id: uuid.UUID, idx: int):
    _clip(job_id, idx)
    return gemini.moderation_request(str(job_id), idx)


@app.post("/jobs/{job_id}/clips/{idx}/moderation", dependencies=A)
def moderation_result(job_id: uuid.UUID, idx: int, resp: dict):
    _clip(job_id, idx)
    return gemini.save_moderation(str(job_id), idx, resp)


@app.post("/jobs/{job_id}/trends", dependencies=A)
def job_trends(job_id: uuid.UUID):
    """Trend hints for the director (n8n step before editing). Skipped when off, no key, or no game recognised."""
    _job(job_id)
    return pipeline.trend_hints(str(job_id))


@app.get("/jobs/{job_id}/text-score-request", dependencies=A)
def text_score_request(job_id: uuid.UUID):
    _job(job_id)
    from . import textscore
    return textscore.request(str(job_id))


@app.post("/jobs/{job_id}/text-score", dependencies=A)
def text_score_save(job_id: uuid.UUID, resp: dict):
    _job(job_id)
    from . import textscore
    return textscore.save(str(job_id), resp)


@app.post("/jobs/{job_id}/llm/rescue", dependencies=A)
def llm_rescue(job_id: uuid.UUID, resp: dict, kind: str = Query(pattern="^(rank|director|review|moderation|textscore)$"), idx: int = Query(ge=0),
               pass_: str = Query("screen", alias="pass", pattern="^(screen|judge)$")):
    """Called by n8n after every Gemini call: passes good answers through, rescues 429/5xx failures via OpenRouter/Groq."""
    _job(job_id)
    from . import fallback
    jid = str(job_id)
    build = {"rank": lambda: gemini.rank_request(jid, idx, pass_),
             "director": lambda: gemini.director_request(jid, idx),
             "review": lambda: gemini.review_request(jid, idx),
             "moderation": lambda: gemini.moderation_request(jid, idx),
             "textscore": lambda: __import__("clipper.textscore", fromlist=["request"]).request(jid)}[kind]
    return fallback.rescue(jid, kind, resp, build)


@app.post("/jobs/{job_id}/clips/{idx}/music", dependencies=A)
def clip_music(job_id: uuid.UUID, idx: int):
    _clip(job_id, idx)
    from .assets import add_music
    try:
        return add_music(str(job_id), idx)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/jobs/{job_id}/clips/{idx}/assets", dependencies=A)
def clip_assets(job_id: uuid.UUID, idx: int):
    _clip(job_id, idx)
    try:
        return assets.resolve(str(job_id), idx)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.post("/jobs/{job_id}/clips/{idx}/render", dependencies=A)
def clip_render(job_id: uuid.UUID, idx: int, force: bool = False):
    c = _clip(job_id, idx)
    if c["status"] not in render.RENDERABLE or not c["edl"]:
        raise HTTPException(409, f"clip {idx} cannot be rendered yet (status {c['status']}); it needs a validated plan and assets")
    st = queue.enqueue(str(job_id), "render", idx, force=force)
    return {"stage": "render", "status": st["status"]}


@app.get("/jobs/{job_id}/clips/{idx}", dependencies=A)
def clip_status(job_id: uuid.UUID, idx: int):
    c = _clip(job_id, idx)
    st = {s["name"]: s for s in db.q("select name,status,error,attempts from stages where job_id=%s and clip_idx=%s", (job_id, idx))}
    busy = any(s["status"] in ("queued", "running") for s in st.values())
    return {"idx": idx, "status": c["status"], "busy": busy, "review": c["review"], "qc": c["qc"], "stages": st,
            "file": f"{config.PUBLIC_BASE}/files/{job_id}/{Path(c['output_path']).name}" if c["output_path"] else None,
            "local_path": c["output_path"], "edl": c["edl"], "candidate": c["candidate"], "assets": c["assets"]}


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = None


@app.post("/jobs/{job_id}/clips/{idx}/review", dependencies=A)
def review(job_id: uuid.UUID, idx: int, r: ReviewIn):
    c = _clip(job_id, idx)
    if r.decision == "approved" and c["status"] != "ready_for_review":
        raise HTTPException(409, f"clip is {c['status']}; only QC-passed clips can be approved")
    db.q("update clips set review=%s, review_note=%s where job_id=%s and idx=%s", (r.decision, r.note, job_id, idx))
    db.event(str(job_id), "info", "review", f"clip {idx} {r.decision}", {"note": r.note})
    return {"idx": idx, "review": r.decision}


@app.post("/jobs/{job_id}/finish", dependencies=A)
def finish(job_id: uuid.UUID):
    _job(job_id)
    pipeline.finish_job(str(job_id))
    return job_status(job_id)


@app.post("/jobs/{job_id}/cancel", dependencies=A)
def cancel_job(job_id: uuid.UUID):
    """Stop a job: queued stages are cancelled now; a stage already running finishes its current step and stops there."""
    jid = str(job_id)
    _job(jid)
    n = db.q("update stages set status='cancelled', finished_at=now() where job_id=%s and status='queued' returning name", (jid,))
    db.set_job(jid, _force=True, status="cancelled", error="cancelled by the owner")
    db.event(jid, "warn", "cancel", "job cancelled", {"stages": len(n)})
    return {"job_id": jid, "status": "cancelled", "cancelled_stages": len(n)}


@app.post("/jobs/{job_id}/retry", dependencies=A)
def retry_job(job_id: uuid.UUID):
    """Retry failed/cancelled work without redoing finished stages (analysis and ranking are reused)."""
    jid = str(job_id)
    j = _job(jid)
    if _busy(jid):
        raise HTTPException(409, "this job is still working")
    db.q("delete from stages where job_id=%s and status in ('failed','cancelled')", (jid,))
    for f in (config.job_dir(jid) / "analysis").glob("edl_*_last_error.json"):  # a retried clip gets its full plan attempts
        f.unlink(missing_ok=True)
    db.q("update clips set status=case when edl is null then 'selected' else 'planned' end, fail_kind=null "
         "where job_id=%s and status in ('failed','qc_failed')", (jid,))
    done = {s["name"] for s in db.q("select name from stages where job_id=%s and clip_idx=-1 and status='done'", (jid,))}
    status = ("selected" if db.q1("select 1 from clips where job_id=%s", (jid,)) else
              "analyzed" if "analyze" in done else "ingested" if "ingest" in done else "created")
    meta = dict(j["meta"] or {})
    meta["t0"] = time.time()
    db.set_job(jid, _force=True, status=status, error=None, meta=meta)
    what = pipeline.resume(jid)
    db.event(jid, "info", "retry", f"retry requested; resuming at {status} ({what})", {})
    return {"job_id": jid, "status": status, "queued": what}


def _busy(job_id: str) -> bool:
    return bool(db.q1("select 1 from stages where job_id=%s and status in ('queued','running')", (job_id,)))


@app.delete("/dashboard/jobs/{job_id}", dependencies=A)
def delete_job(job_id: uuid.UUID, source: bool = False, force: bool = False):
    """Remove a job completely: its database rows, its folder (clips, analysis, renders) and, if asked, the uploaded source
    file in the inbox (only when no other job uses it). Refuses while a stage is running unless force=1."""
    jid = str(job_id)
    j = _job(jid)
    if _busy(jid) and not force:
        raise HTTPException(409, "this job is still working. Wait for it to finish, or delete it with force")
    # the job folder holds a hard link to the uploaded video: find the inbox file by inode before the folder is removed
    inbox_copy = None
    sp = Path(j["source_path"]) if j.get("source_path") else None
    if source and sp and sp.exists():
        try:
            ino = sp.stat().st_ino
            shared = any(Path(o["source_path"]).exists() and Path(o["source_path"]).stat().st_ino == ino
                         for o in db.q("select source_path from jobs where id<>%s and source_path is not null", (jid,)))
            if not shared:
                inbox_copy = next((f for f in config.INBOX.iterdir() if f.is_file() and f.stat().st_ino == ino), None)
        except OSError:
            inbox_copy = None
    freed = 0
    d = config.JOBS / jid
    if d.exists():
        freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink())
        shutil.rmtree(d, ignore_errors=True)
    removed_source = None
    if inbox_copy and inbox_copy.exists():
        freed += inbox_copy.stat().st_size
        inbox_copy.unlink()
        removed_source = inbox_copy.name
    db.q("delete from events where job_id=%s", (jid,))
    db.q("delete from jobs where id=%s", (jid,))  # stages, clips, costs, posts cascade
    return {"deleted": jid, "freed_mb": round(freed / 1e6, 1), "source_removed": removed_source}


@app.post("/dashboard/jobs/{job_id}/restart", dependencies=A)
def restart_job(job_id: uuid.UUID):
    """Start over from the original video: forget the analysis, picks, edits and renders, then run the whole pipeline again."""
    jid = str(job_id)
    j = _job(jid)
    if _busy(jid):
        raise HTTPException(409, "this job is still working")
    inbox_name = None  # where the video came from (shown to the caller)
    sp = Path(j["source_path"]) if j.get("source_path") else None
    if sp and sp.exists():
        try:
            ino = sp.stat().st_ino
            inbox_name = next((f.name for f in config.INBOX.iterdir() if f.is_file() and f.stat().st_ino == ino), None)
        except OSError:
            pass
    d = config.JOBS / jid
    if d.exists():
        for child in d.iterdir():  # keep only the source video
            if child.name.startswith("source.") and child.is_file():
                continue
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
    db.q("delete from clips where job_id=%s", (jid,))
    db.q("delete from stages where job_id=%s", (jid,))
    meta = dict(j["meta"] or {})
    meta["t0"] = time.time()
    for k in ("trends", "tasks"):
        meta.pop(k, None)
    db.set_job(jid, _force=True, status="created", error=None, meta=meta)
    db.event(jid, "info", "create", "started over from the dashboard", {})
    queue.enqueue(jid, "ingest", force=True)
    m = j["meta"] or {}
    ln = m.get("length") or [None, None]
    return {"job_id": jid, "status": "created", "source_type": j["source_type"], "url": j.get("source_url"), "filename": inbox_name,
            "title": j.get("title"), "style": m.get("style"), "clips": m.get("clips"), "min_seconds": ln[0], "max_seconds": ln[1]}


@app.post("/watch/check", dependencies=A)
def watch_check():
    """New videos on the watched channels (metadata only). Never starts a job."""
    from . import watch
    from .manage import load_settings
    s = load_settings()
    if not s.watch_enabled or not s.watch_channels:
        return {"checked": 0, "new": [], "errors": [], "skipped": "watching is off or no channels set"}
    return watch.check(s.watch_channels)


def _live_submit(stream: dict, filename: str, started_at: float) -> str:
    """A finished live chunk becomes a normal job (rights were confirmed when the stream was added)."""
    j = JobIn(source_type="file", filename=filename, rights_confirmed=True, style=stream.get("style"),
              clips=stream.get("clips"), game=stream.get("game") or None, engine="stories",
              captions=stream.get("captions", "off"), template=stream.get("template", "gta6_gameplay"),
              title=f"{stream['channel']} live {datetime.fromtimestamp(started_at).strftime('%b %d %H:%M')}")
    jid = create_job(j)["job_id"]
    meta = dict(db.q1("select meta from jobs where id=%s", (jid,))["meta"] or {})
    meta["live"] = {"stream": stream["name"], "platform": stream["platform"], "channel": stream["channel"],
                    "started_at": started_at}
    meta.update({"mode": "viral", "content_type": "stream", "edit": "balanced", "silence": "medium",
                 "zoom": "off" if stream.get("template", "gta6_gameplay") == "gta6_gameplay" else "medium",
                 "sfx": "off" if stream.get("template", "gta6_gameplay") == "gta6_gameplay" else "medium",
                 "caption_look": "clean", "gameplay_crop": stream.get("gameplay_crop", "auto"),
                 "facecam_size": stream.get("facecam_size", "medium"),
                 "facecam_position": stream.get("facecam_position", "top_left"), "game_logo": "gta6"})
    db.set_job(jid, meta=meta)
    return jid


class LiveStreamIn(BaseModel):
    ref: str = Field(min_length=3, max_length=200, description="twitch:name, kick:name, youtube:@handle or a channel link")
    rights_confirmed: bool
    topic: str = Field("", max_length=200, description="comma-separated keywords; record only while the title matches (empty = always)")
    game: str = Field("", max_length=60)
    auto_clip: bool = True
    clips: int = Field(3, ge=1, le=8)
    style: str | None = Field(None, pattern="^(viral|funny|cinematic|fast|minimal)$")
    template: str = Field("gta6_gameplay", pattern="^(auto|full|split_50|split_20|gta6_gameplay)$")
    captions: str = Field("off", pattern="^(overlay|bar|off)$")
    gameplay_crop: str = Field("auto", pattern="^(auto|left|center|right|fit)$")
    facecam_size: str = Field("medium", pattern="^(small|medium|large)$")
    facecam_position: str = Field("top_left", pattern="^(top_left|top_right|mid_left|mid_right)$")


class LiveStreamPatch(BaseModel):
    enabled: bool | None = None
    auto_clip: bool | None = None
    topic: str | None = Field(None, max_length=200)
    game: str | None = Field(None, max_length=60)
    clips: int | None = Field(None, ge=1, le=8)
    template: str | None = Field(None, pattern="^(auto|full|split_50|split_20|gta6_gameplay)$")
    captions: str | None = Field(None, pattern="^(overlay|bar|off)$")
    gameplay_crop: str | None = Field(None, pattern="^(auto|left|center|right|fit)$")
    facecam_size: str | None = Field(None, pattern="^(small|medium|large)$")
    facecam_position: str | None = Field(None, pattern="^(top_left|top_right|mid_left|mid_right)$")


@app.get("/dashboard/live", dependencies=A)
def live_status():
    from . import live, twitchclips
    return {"enabled": live.ENABLED, "streams": live.status(), "poll_s": live.POLL_S, "chunk_s": live.CHUNK_S,
            "twitch_clips_configured": twitchclips.configured(),
            "free_gb": round(shutil.disk_usage(config.DATA).free / 1e9, 1)}


@app.post("/dashboard/live", dependencies=A)
def live_add(b: LiveStreamIn):
    from . import live
    try:
        s = live.add_stream(b.ref, b.rights_confirmed, b.topic, b.game, b.auto_clip, b.clips, b.style,
                            template=b.template, captions=b.captions, gameplay_crop=b.gameplay_crop,
                            facecam_size=b.facecam_size, facecam_position=b.facecam_position)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {k: v for k, v in s.items() if k != "rights_confirmed"}


@app.put("/dashboard/live/{name}", dependencies=A)
def live_update(name: str, b: LiveStreamPatch):
    from . import live
    try:
        return live.update_stream(name, **b.model_dump())
    except KeyError as e:
        raise HTTPException(404, "no such stream") from e


@app.delete("/dashboard/live/{name}", dependencies=A)
def live_remove(name: str):
    from . import live
    if not live.remove_stream(name):
        raise HTTPException(404, "no such stream")
    return {"removed": name}


@app.post("/dashboard/live/check", dependencies=A)
def live_check():
    from . import live
    live.check_now()
    return {"ok": True}


@app.post("/cleanup", dependencies=A)
def cleanup(days: int | None = None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days if days is not None else config.RETENTION_DAYS)
    old = db.q("select id from jobs where updated_at < %s", (cutoff,))
    freed = 0
    for j in old:
        d = config.JOBS / str(j["id"])
        if d.exists():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
        db.q("update jobs set status='expired' where id=%s", (j["id"],))
    return {"expired_jobs": len(old), "freed_mb": round(freed / 1e6, 1)}


@app.get("/files/{job_id}/{name}")
def files(job_id: uuid.UUID, name: str):
    p = (config.JOBS / str(job_id) / "output" / Path(name).name)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/jobs/{job_id}/source")
def source_file(job_id: uuid.UUID):
    """Download the original source saved for a job, including live recording pieces."""
    j = _job(job_id)
    p = Path(j.get("source_path") or "")
    if not p.is_file():
        raise HTTPException(404, "raw source is not ready")
    return FileResponse(p, filename=p.name, media_type="video/mp4", content_disposition_type="attachment")


def _sig(job_id, idx) -> str:
    import hmac
    return hmac.new(config.TOKEN.encode(), f"review:{job_id}:{idx}".encode(), "sha256").hexdigest()[:32]


@app.post("/ui/review/{job_id}/{idx}/{decision}/{sig}", response_class=HTMLResponse)
def ui_review(job_id: uuid.UUID, idx: int, decision: str, sig: str):
    """Review buttons on the local results page. Signed per clip, so the page needs no token."""
    import hmac
    if decision not in ("approved", "rejected") or not hmac.compare_digest(sig, _sig(job_id, idx)):
        raise HTTPException(403, "bad review link")
    review(job_id, idx, ReviewIn(decision=decision))
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/jobs/{job_id}/view">')


from . import ui  # noqa: E402,F401  (dashboard routes)
from . import manage  # noqa: E402,F401  (owner dashboard API)
from . import guide  # noqa: E402,F401  (Tools page: edit guide)
from . import ops  # noqa: E402,F401  (/ready, GPU telemetry, admin, idle auto-stop)
from . import uploads  # noqa: E402,F401  (chunked video upload)
