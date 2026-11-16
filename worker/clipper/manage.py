"""Owner dashboard API: overview, clip library, settings, costs, health, publishing drafts, accounts.
All routes need the worker token (the Next.js dashboard calls them server-side). Nothing here publishes."""
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import config, db
from .main import A, app
from .styles import STYLES

ACTIVE = ("created", "ingested", "analyzed", "ranking", "selected", "rendering")
SCREEN_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
MAIN_MODELS = ["gemini-3.8-flash", "gemini-4-hydrogen", "gemini-3.1-flash-lite"]
SETTINGS_FILE = config.DATA / "settings.json"
STAGE_ORDER = ["ingest", "analyze", "rank", "select", "director", "assets", "render", "qc", "review"]

db.q("""create table if not exists posts (
  id uuid primary key, job_id uuid not null references jobs(id) on delete cascade, idx int not null,
  platform text not null, title text, caption text, hashtags text, status text not null default 'draft',
  scheduled_at timestamptz, created_at timestamptz not null default now(), updated_at timestamptz not null default now())""")
for col in ("provider text", "external_id text", "media_url text", "error text", "approved_at timestamptz", "sent_at timestamptz", "published_at timestamptz"):
    db.q(f"alter table posts add column if not exists {col}")


# ---------------------------------------------------------------- settings
class Settings(BaseModel):
    style: str = Field("viral", pattern="^(viral|funny|cinematic|fast|minimal)$")
    clips: int | None = Field(None, ge=1, le=8)
    min_seconds: int | None = Field(None, ge=8, le=150)
    max_seconds: int | None = Field(None, ge=10, le=180)
    cut_mode: Literal["auto", "continuous", "light", "tight"] = "auto"
    review_min: float = Field(5.0, ge=0, le=10)
    review_redos: int = Field(1, ge=0, le=2)
    screen_model: Literal["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"] = "gemini-3.1-flash-lite"
    main_model: Literal["gemini-3.8-flash", "gemini-4-hydrogen", "gemini-3.1-flash-lite"] = "gemini-3.8-flash"
    judge_top: int = Field(3, ge=0, le=6)
    budget_job: float = Field(0.10, ge=0.01, le=5)
    budget_day: float = Field(0.50, ge=0.01, le=50)
    budget_total: float = Field(4.00, ge=0.01, le=500)
    budget_since: str = ""  # set once, when the first paid settings are saved
    fallback_enabled: bool = True
    moderation_enabled: bool = False  # optional Gemini NSFW check of every finished clip
    publishing_enabled: bool = False  # the Publish button stays locked until the owner turns this on
    publish_provider: Literal["ayrshare", "postlake", "publora"] = "ayrshare"
    trends_enabled: bool = True
    trends_provider: Literal["auto", "shortsintel", "trendsmcp"] = "auto"
    detect_burned_captions: bool = True
    music_enabled: bool = False  # owner, 2026-11-15: no music in clips
    music_gain_db: float = Field(-22, ge=-40, le=-10)
    music_duck_db: float = Field(-10, ge=-30, le=0)
    watch_channels: list[str] = Field(default_factory=list, max_length=30)  # YouTube channel ids, URLs or @handles
    watch_enabled: bool = False  # n8n checks every 30 min and sends a Telegram link per new video (never auto-clips)
    transcript_scoring: bool = True  # read the whole transcript first and score moments 0-100 (one cheap text call)
    dead_air_cut: bool = True  # cut stretches inside a clip where nobody talks and nothing happens
    captions: Literal["overlay", "bar", "off"] = "overlay"  # bar = own strip between webcam and gameplay (split layout)
    render_engine: Literal["fast", "remotion"] = "fast"  # fast = one ffmpeg pass (~10 s/clip); remotion = Chrome (~4 min/clip)
    retention_days: int = Field(7, ge=1, le=90)
    telegram_notify: bool = True


def load_settings() -> Settings:
    try:
        raw = json.loads(SETTINGS_FILE.read_text())
        raw.pop("gemini_model", None)  # replaced by screen_model / main_model
        return Settings.model_validate(raw)
    except (OSError, ValueError):
        return Settings(review_min=config.REVIEW_MIN, review_redos=config.REVIEW_REDOS, retention_days=config.RETENTION_DAYS)


def apply_settings(s: Settings):
    config.REVIEW_MIN, config.REVIEW_REDOS, config.RETENTION_DAYS = s.review_min, s.review_redos, s.retention_days
    config.SCREEN_MODEL, config.MAIN_MODEL, config.JUDGE_TOP = s.screen_model, s.main_model, s.judge_top
    config.BUDGET_JOB, config.BUDGET_DAY, config.BUDGET_TOTAL = s.budget_job, s.budget_day, s.budget_total
    config.BUDGET_SINCE = s.budget_since
    config.FALLBACK_ENABLED = s.fallback_enabled


def _init_settings():
    s = load_settings()
    if not s.budget_since:  # billing was enabled on 2026-11-14; earlier usage was free tier and does not count
        s.budget_since = datetime.now(timezone.utc).isoformat(timespec="seconds")
        SETTINGS_FILE.write_text(s.model_dump_json(indent=1))
    apply_settings(s)


_init_settings()


def integrations() -> dict:
    """Which optional services have a key in .env (True/False only; values are never returned)."""
    from . import fallback, music, publish, trends
    return {**{f"music_{k}": v for k, v in music.keys().items()}, **{f"llm_{k}": v for k, v in fallback.keys().items()},
            **{f"trends_{k}": v for k, v in trends.keys().items()}, "publish_ayrshare": publish.Ayrshare().configured(),
            "gemini": bool(os.environ.get("GEMINI_API_KEY")), "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))}


@app.get("/dashboard/settings", dependencies=A)
def get_settings():
    from . import budget
    models = lambda ids: [{"id": m, "usd_in": config.price(m)[0], "usd_out": config.price(m)[1]} for m in ids]  # noqa: E731
    return {"settings": load_settings().model_dump(), "screen_models": models(SCREEN_MODELS), "main_models": models(MAIN_MODELS),
            "budget": budget.status(), "integrations": integrations(),
            "styles": [{"id": k, "label": v["label"]} for k, v in STYLES.items()]}


@app.put("/dashboard/settings", dependencies=A)
def put_settings(s: Settings):
    s.budget_since = load_settings().budget_since or s.budget_since
    if s.min_seconds and s.max_seconds and s.max_seconds < s.min_seconds + 5:
        raise HTTPException(400, "Maximum length must be at least 5 s above the minimum.")
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(s.model_dump_json(indent=1))
    tmp.replace(SETTINGS_FILE)
    apply_settings(s)
    return {"settings": s.model_dump()}


# ---------------------------------------------------------------- health / overview
def health() -> dict:
    out = {}
    try:
        db.q1("select 1 as ok")
        out["database"] = {"ok": True, "detail": "Postgres reachable"}
    except Exception as e:  # noqa: BLE001
        out["database"] = {"ok": False, "detail": str(e)[:120]}
    from . import ops
    g = ops.gpu()
    cached = (ops._ready_cache.get("v") or {}).get("checks", {})
    ff = cached.get("ffmpeg", {})
    if g.get("present"):
        enc = "NVENC verified" if ff.get("nvenc_test_encode") else "NVENC not verified yet" if not ff else "NVENC FAILED"
        out["gpu"] = {"ok": bool(ff.get("nvenc_test_encode", True)), "detail": f"{g['name']} · {(g['util_pct'] or 0):.0f}% busy · {enc}",
                      "name": g["name"], "util_pct": g["util_pct"]}
    else:
        out["gpu"] = {"ok": False, "detail": "no NVIDIA GPU on this host (rendering uses the CPU/Apple encoder)"}
    out["pipeline"] = {"ok": True, "detail": "job engine runs inside the worker (no n8n needed)"}
    du = shutil.disk_usage(config.DATA)
    used = sum(f.stat().st_size for f in config.JOBS.rglob("*") if f.is_file()) if config.JOBS.exists() else 0
    out["disk"] = {"ok": du.free > 5e9, "detail": f"{du.free / 1e9:.0f} GB free", "free_gb": round(du.free / 1e9, 1), "jobs_gb": round(used / 1e9, 2)}
    q = db.q1("select count(*) filter (where status='running') as running, count(*) filter (where status='queued') as queued from stages")
    out["worker"] = {"ok": True, "detail": f"{q['running']} running, {q['queued']} queued", "running": q["running"], "queued": q["queued"]}
    return out


def _clip_row(c) -> dict:
    cand, edl, rv = c["candidate"] or {}, c["edl"] or {}, c.get("ai_review") or {}
    from .edl import social_copy
    social_caption, hashtags = social_copy(edl)
    name = Path(c["output_path"]).name if c["output_path"] else None
    segs = edl.get("segments") or []
    return {"job_id": str(c["job_id"]), "idx": c["idx"], "job_title": c["job_title"], "title": edl.get("title") or cand.get("title") or "Untitled clip",
            "description": edl.get("description") or cand.get("why_engaging"), "hook": (edl.get("hook") or {}).get("text"),
            "tiktok_caption": social_caption, "hashtags": hashtags,
            "status": c["status"], "review": c["review"], "style": edl.get("style") or c.get("style"),
            "content_type": cand.get("content_type"), "moment_type": edl.get("moment_type"),
            "quality": cand.get("quality"), "ai_score": rv.get("score"), "below_quality_bar": bool(cand.get("below_quality_bar")),
            "duration": round(sum((s["end"] - s["start"]) / (s.get("speed") or 1.0) for s in segs), 1) if segs else None,
            "file": f"/files/{c['job_id']}/{name}" if name else None, "thumbnail": f"/thumbs/{c['job_id']}/{name}" if name else None,
            "created_at": c["created_at"].isoformat()}


CLIP_SQL = """select c.*, j.title as job_title, j.created_at from clips c join jobs j on j.id=c.job_id"""


@app.get("/dashboard/overview", dependencies=A)
def overview():
    now = datetime.now(timezone.utc)
    running = db.q("select id, title, status, created_at, meta from jobs where status = any(%s) order by created_at desc", (list(ACTIVE),))
    live = []
    for j in running:
        st = db.q("select name, clip_idx, status from stages where job_id=%s", (j["id"],))
        current = next((s for s in st if s["status"] == "running"), None) or next((s for s in st if s["status"] == "queued"), None)
        done = {s["name"] for s in st if s["status"] == "done"}
        step = current["name"] if current else ("rank" if j["status"] in ("analyzed", "ranking") else "director" if j["status"] == "selected" else "ingest")
        live.append({"id": str(j["id"]), "title": j["title"] or str(j["id"])[:8], "status": j["status"], "step": step,
                     "progress": round(len(done & set(STAGE_ORDER)) / len(STAGE_ORDER), 2),
                     "elapsed_s": round((now - j["created_at"]).total_seconds())})
    review = [_clip_row(c) for c in db.q(CLIP_SQL + " where c.status='ready_for_review' and c.review='pending' order by j.created_at desc limit 12")]
    cost = db.q1("""select coalesce(sum(usd) filter (where ts > now() - interval '1 day'),0) as day,
                           coalesce(sum(usd) filter (where ts > now() - interval '7 days'),0) as week,
                           coalesce(sum(usd),0) as total, count(*) filter (where ts > now() - interval '1 day') as calls_day from costs""")
    counts = db.q1("""select count(*) as jobs, count(*) filter (where status='done') as done,
                             (select count(*) from clips where output_path is not null) as clips,
                             (select count(*) from clips where review='approved') as approved from jobs""")
    return {"live": live, "review": review, "cost": {k: float(v) for k, v in cost.items()}, "counts": counts, "health": health(),
            "settings": load_settings().model_dump()}


# ---------------------------------------------------------------- clip library
@app.get("/dashboard/clips", dependencies=A)
def clips(status: str | None = None, q: str | None = None, limit: int = 200):
    where, args = ["c.output_path is not null"], []
    if status == "review":
        where.append("c.status='ready_for_review' and c.review='pending'")
    elif status in ("approved", "rejected"):
        where.append("c.review=%s"); args.append(status)
    elif status == "failed":
        where.append("c.status in ('failed','qc_failed')")
    if q:
        where.append("(j.title ilike %s or c.edl->>'title' ilike %s or c.candidate->>'title' ilike %s)"); args += [f"%{q}%"] * 3
    rows = db.q(CLIP_SQL + " where " + " and ".join(where) + " order by j.created_at desc, c.idx limit %s", (*args, min(limit, 500)))
    return {"clips": [_clip_row(c) for c in rows]}


# ---------------------------------------------------------------- costs
@app.get("/dashboard/costs", dependencies=A)
def costs():
    days = db.q("""select to_char(date_trunc('day', ts), 'YYYY-MM-DD') as day, count(*) as calls, coalesce(sum(usd),0) as usd
                   from costs where ts > now() - interval '30 days' group by 1 order by 1""")
    models = db.q("""select coalesce(units->>'model','unknown') as model, count(*) as calls,
                     coalesce(sum(coalesce((units->>'in')::bigint,0)),0) as tokens_in, coalesce(sum(coalesce((units->>'out')::bigint,0)),0) as tokens_out,
                     coalesce(sum(usd),0) as usd from costs group by 1 order by usd desc""")
    kinds = db.q("""select split_part(item, ':', 1) as kind, count(*) as calls, coalesce(sum(usd),0) as usd from costs group by 1 order by usd desc""")
    jobs = db.q("""select j.id, j.title, j.created_at, count(k.*) as calls, coalesce(sum(k.usd),0) as usd,
                   (select count(*) from clips c where c.job_id=j.id and c.output_path is not null) as clips
                   from jobs j left join costs k on k.job_id=j.id group by j.id order by j.created_at desc limit 50""")
    num = lambda rows: [{k: (float(v) if k == "usd" else v) for k, v in r.items()} for r in rows]  # noqa: E731
    from . import budget
    return {"budget": budget.status(), "days": num(days), "models": num(models), "kinds": num(kinds),
            "jobs": [{**r, "id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in num(jobs)],
            "note": "Estimated at Google's paid rates. Usage before billing started (Nov 14) ran on the free tier."}


# ---------------------------------------------------------------- publishing (drafts only)
PLATFORMS = {
    "youtube": {"label": "YouTube Shorts", "title_max": 100, "caption_max": 5000,
                "needs": ["Google Cloud project (free)", "YouTube Data API v3 enabled", "OAuth client for this app"]},
    "instagram": {"label": "Instagram Reels", "title_max": 0, "caption_max": 2200,
                  "needs": ["Instagram Business or Creator account", "Facebook Page linked to it", "Meta developer app with Instagram Graph API"]},
    "tiktok": {"label": "TikTok", "title_max": 0, "caption_max": 2200,
               "needs": ["TikTok developer app", "Content Posting API access", "Until TikTok audits the app, posts can only be private"]},
}


class PostIn(BaseModel):
    job_id: uuid.UUID
    idx: int = Field(ge=0)
    platform: Literal["youtube", "instagram", "tiktok"]
    title: str | None = Field(None, max_length=100)
    caption: str | None = Field(None, max_length=5000)
    hashtags: str | None = Field(None, max_length=500)
    scheduled_at: datetime | None = None


class PostPatch(BaseModel):
    title: str | None = Field(None, max_length=100)
    caption: str | None = Field(None, max_length=5000)
    hashtags: str | None = Field(None, max_length=500)
    scheduled_at: datetime | None = None
    status: Literal["draft", "scheduled", "approved"] | None = None


def _post(p) -> dict:
    c = db.q1(CLIP_SQL + " where c.job_id=%s and c.idx=%s", (p["job_id"], p["idx"]))
    return {**{k: (v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, uuid.UUID) else v) for k, v in p.items()},
            "clip": _clip_row(c) if c else None}


@app.get("/dashboard/posts", dependencies=A)
def list_posts():
    return {"posts": [_post(p) for p in db.q("select * from posts order by coalesce(scheduled_at, created_at) desc limit 200")],
            **accounts()}


@app.post("/dashboard/posts", dependencies=A)
def create_post(p: PostIn):
    c = db.q1("select * from clips where job_id=%s and idx=%s", (p.job_id, p.idx))
    if not c or not c["output_path"]:
        raise HTTPException(404, "clip not found or not rendered")
    edl = c["edl"] or {}
    from .edl import social_copy
    social_caption, social_hashtags = social_copy(edl)
    pid = uuid.uuid4()
    db.q("insert into posts(id, job_id, idx, platform, title, caption, hashtags, scheduled_at) values (%s,%s,%s,%s,%s,%s,%s,%s)",
         (pid, p.job_id, p.idx, p.platform, p.title if p.title is not None else (edl.get("title") or "")[:100],
          p.caption if p.caption is not None else social_caption,
          p.hashtags if p.hashtags is not None else " ".join(social_hashtags), p.scheduled_at))
    return _post(db.q1("select * from posts where id=%s", (pid,)))


@app.put("/dashboard/posts/{post_id}", dependencies=A)
def update_post(post_id: uuid.UUID, p: PostPatch):
    cur = db.q1("select status from posts where id=%s", (post_id,))
    if not cur:
        raise HTTPException(404, "post not found")
    if cur["status"] in ("sent", "published"):
        raise HTTPException(409, "this post was already sent to the provider; edit it there")
    fields = {k: v for k, v in p.model_dump(exclude_unset=True).items()}
    if cur["status"] == "approved" and fields.keys() & {"title", "caption", "hashtags"}:
        fields["status"] = "draft"  # changed after approval: the owner must approve the new text again
    if fields:
        db.q("update posts set " + ", ".join(f"{k}=%s" for k in fields) + ", updated_at=now() where id=%s", (*fields.values(), post_id))
    return _post(db.q1("select * from posts where id=%s", (post_id,)))


@app.delete("/dashboard/posts/{post_id}", dependencies=A)
def delete_post(post_id: uuid.UUID):
    db.q("delete from posts where id=%s", (post_id,))
    return {"deleted": str(post_id)}


@app.get("/dashboard/accounts", dependencies=A)
def accounts():
    from . import publish as pubm
    s = load_settings()
    pub = pubm.provider(s.publish_provider)
    return {"accounts": [{"platform": k, **v, "connected": pub.configured(), "via": pub.name} for k, v in PLATFORMS.items()],
            "provider": pub.name, "provider_configured": pub.configured(), "publishing_enabled": s.publishing_enabled}


@app.post("/dashboard/posts/{post_id}/approve", dependencies=A)
def approve_post(post_id: uuid.UUID):
    p = db.q1("select * from posts where id=%s", (post_id,))
    if not p:
        raise HTTPException(404, "post not found")
    if p["status"] not in ("draft", "scheduled"):
        raise HTTPException(409, f"post is {p['status']}")
    db.q("update posts set status='approved', approved_at=now(), updated_at=now() where id=%s", (post_id,))
    return _post(db.q1("select * from posts where id=%s", (post_id,)))


class ActionIn(BaseModel):
    action: Literal["send", "publish"]


@app.post("/dashboard/posts/{post_id}/action", dependencies=A)
def post_action(post_id: uuid.UUID, a: ActionIn):
    """Called by the n8n Publish workflow. send = create a held draft at the provider; publish = approve it there."""
    from . import publish as pubm
    p = db.q1("select * from posts where id=%s", (post_id,))
    if not p:
        raise HTTPException(404, "post not found")
    s = load_settings()
    pub = pubm.provider(s.publish_provider)
    why = pubm.gate(a.action, p, s.publishing_enabled, pub)
    if why:
        raise HTTPException(409, why)
    try:
        if a.action == "send":
            c = db.q1("select output_path from clips where job_id=%s and idx=%s", (p["job_id"], p["idx"]))
            if not c or not c["output_path"] or not Path(c["output_path"]).is_file():
                raise HTTPException(409, "the clip file is missing (cleaned up?)")
            r = pub.create_draft({**p, "id": str(p["id"])}, Path(c["output_path"]))
            db.q("update posts set status='sent', provider=%s, external_id=%s, media_url=%s, error=null, sent_at=now(), updated_at=now() where id=%s",
                 (pub.name, r["external_id"], r.get("media_url"), post_id))
        else:
            pub.publish(p["external_id"])
            db.q("update posts set status='published', error=null, published_at=now(), updated_at=now() where id=%s", (post_id,))
        db.add_cost(str(p["job_id"]), f"publish:{a.action}", {"model": pub.name, "in": 0, "out": 0, "platform": p["platform"]}, 0)
    except pubm.PublishError as e:
        db.q("update posts set error=%s, updated_at=now() where id=%s", (str(e)[:500], post_id))
        raise HTTPException(502, str(e)) from e
    return _post(db.q1("select * from posts where id=%s", (post_id,)))
