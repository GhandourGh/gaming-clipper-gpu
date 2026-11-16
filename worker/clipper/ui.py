"""Local dashboard: submit (URL or upload) -> choose style -> generate -> preview -> download.
Served by the worker on 127.0.0.1 only. Mutating UI routes check the Origin header
(blocks cross-site form posts) and never expose the API token to the page."""
import hashlib
import hmac
import html
import io
import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from fastapi import Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from . import config, db, ingest, media
from .main import A, _clip, _job, _sig, app, job_status
from .styles import STYLES

e = html.escape


def _file_bytes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        path = Path(value)
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _same_origin(req: Request):
    origin = req.headers.get("origin") or req.headers.get("referer") or ""
    if not origin.startswith((f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}")):
        raise HTTPException(403, "cross-origin request blocked")


CSS = """:root{color-scheme:light dark;--bg:#0f1115;--card:#181b22;--soft:#20242d;--fg:#e8eaf0;--muted:#9aa3b2;--acc:#ffd400;--line:#2a2f3a;--green:#3ddc84;--red:#ff6b6b;--blue:#69a7ff}
@media (prefers-color-scheme: light){:root{--bg:#f5f6f8;--card:#fff;--soft:#f0f2f5;--fg:#14161a;--muted:#5d6675;--line:#dde1e7;--green:#168a4b;--red:#cc3434;--blue:#1769d2}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px}a{color:inherit}h1{font-size:22px;margin:8px 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
form.sub{display:grid;gap:10px;grid-template-columns:1fr 1fr}form.sub .full{grid-column:1/-1}
input,select,button{font:inherit;padding:9px 11px;border-radius:9px;border:1px solid var(--line);background:var(--bg);color:var(--fg);width:100%}
button{background:var(--acc);color:#111;font-weight:700;border:0;cursor:pointer;width:auto}button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}th{color:var(--muted);font-weight:600}
.pill{display:inline-block;padding:3px 9px;border-radius:99px;font-size:12px;border:1px solid var(--line);white-space:nowrap}.pill.done,.pill.approved,.pill.ready_for_review{color:var(--green);border-color:var(--green)}.pill.failed,.pill.rejected{color:var(--red);border-color:var(--red)}.pill.running,.pill.rendering,.pill.selected{color:var(--blue);border-color:var(--blue)}
.ok{color:var(--green)}.bad{color:var(--red)}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
video{width:100%;aspect-ratio:9/16;background:#000;border-radius:10px;display:block}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.row form{display:inline-flex;gap:6px}
dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:13px}dt{color:var(--muted)}
.steps{display:grid;grid-template-columns:repeat(9,minmax(92px,1fr));gap:6px;overflow-x:auto;padding:2px 0 8px}.step{min-height:70px;padding:9px;border-radius:10px;background:var(--soft);border:1px solid var(--line);font-size:12px}.step b{display:block;font-size:13px;margin-bottom:5px}.step.done{border-color:var(--green)}.step.failed{border-color:var(--red)}.step.running{border-color:var(--blue);animation:pulse 1.4s ease-in-out infinite}.step.waiting{color:var(--muted)}@keyframes pulse{50%{box-shadow:0 0 0 3px color-mix(in srgb,var(--blue) 20%,transparent)}}
.clip-steps{grid-template-columns:repeat(5,minmax(110px,1fr))}.pipeline-label{margin:12px 0 5px;font-size:13px;font-weight:700}.why{background:var(--soft);border-radius:10px;padding:12px;margin-top:10px}.why h4{margin:0 0 8px}.qc-list{list-style:none;padding:0;margin:7px 0}.qc-list li{margin:3px 0}.quality-badge{display:inline-block;background:var(--acc);color:#111;border-radius:6px;padding:3px 7px;font-weight:700;font-size:12px}.rejects{margin-top:16px}.rejects summary{cursor:pointer;font-weight:700}.reject-item{padding:9px 0;border-bottom:1px solid var(--line)}
.history-thumb{width:72px;height:48px;object-fit:cover;background:var(--soft);border-radius:7px;display:block}.history-title{display:flex;align-items:center;gap:10px}.how{margin:16px 0}.costs{max-width:620px;margin-top:16px}.nowrap{white-space:nowrap}
@media (max-width:700px){.wrap{padding:10px}form.sub{grid-template-columns:1fr}.grid{grid-template-columns:1fr}.steps{grid-template-columns:repeat(9,105px)}table.history thead{display:none}table.history,table.history tbody,table.history tr,table.history td{display:block;width:100%}table.history tr{padding:12px 0;border-bottom:1px solid var(--line)}table.history td{border:0;padding:3px 0}table.history td:before{content:attr(data-label);color:var(--muted);display:inline-block;width:88px;font-size:12px}.history-title{margin-bottom:6px}.history-title:before{display:none!important}.card{padding:12px}}"""


def page(title, body, refresh=False):
    meta = '<meta http-equiv="refresh" content="10">' if refresh else ""
    return HTMLResponse(f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">{meta}
<title>{e(title)}</title><style>{CSS}</style><div class=wrap><p class=muted><a href="/">← Gaming Clipper</a></p>{body}</div></html>""")


def style_select(name="style", current="viral"):
    opts = "".join(f'<option value="{k}"{" selected" if k == current else ""}>{e(v["label"])}</option>' for k, v in STYLES.items())
    return f'<select name="{name}">{opts}</select>'


STEP_LABELS = (("ingest", "Download"), ("analyze", "Analyse"), ("rank", "Find moments"),
               ("select", "Pick"), ("director", "Edit plan"), ("assets", "Assets"),
               ("render", "Render"), ("qc", "Quality check"), ("review", "Review"))


def _clock(value):
    seconds = max(0, int(float(value or 0)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _step(label, state="waiting", seconds=None, error=None):
    detail = f"✓ {float(seconds):.1f}s" if state == "done" and seconds is not None else {"done": "✓ Done", "running": "● Running", "failed": "! Failed"}.get(state, "Waiting")
    extra = f'<details><summary>Error</summary><span class=bad>{e(str(error))}</span></details>' if error else ""
    return f'<div class="step {state}"><b>{e(label)}</b>{e(detail)}{extra}</div>'


def _static_steps():
    return '<div class=steps>' + "".join(_step(label) for _, label in STEP_LABELS) + "</div>"


def _stage_state(stage, inferred=False):
    if stage:
        status = stage["status"]
        state = "running" if status in ("queued", "running") else "done" if status in ("done", "complete", "completed") else "failed" if status == "failed" else "waiting"
        seconds = (stage.get("result") or {}).get("seconds")
        if seconds is None and stage.get("started_at") and stage.get("finished_at"):
            seconds = (stage["finished_at"] - stage["started_at"]).total_seconds()
        return state, seconds, stage.get("error")
    return ("done", None, None) if inferred else ("waiting", None, None)


def _pipeline(stages, clips, job_status_value):
    lookup = {(x["name"], x["clip_idx"]): x for x in stages}
    shared_done = {"ingest": {"ingested", "analyzed", "ranking", "selected", "no_highlights", "complete", "completed"},
                   "analyze": {"analyzed", "ranking", "selected", "no_highlights", "complete", "completed"},
                   "rank": {"selected", "no_highlights", "complete", "completed"},
                   "select": {"selected", "no_highlights", "complete", "completed"}}
    shared = []
    for key, label in STEP_LABELS[:4]:
        inferred = job_status_value in shared_done[key] or bool(clips)
        state = _stage_state(lookup.get((key, -1)), inferred)
        shared.append(_step(label, *state))
    shared.extend(_step(label) for _, label in STEP_LABELS[4:])
    html_rows = '<div class=pipeline-label>Shared work</div><div class=steps>' + "".join(shared) + "</div>"
    for clip in clips:
        edl, qc = clip["edl"] or {}, clip["qc"] or {}
        inferred = {"director": bool(edl), "assets": bool(clip["assets"]), "render": bool(clip["output_path"]),
                    "qc": bool(qc), "review": clip["review"] in ("approved", "rejected")}
        parts = []
        for key, label in STEP_LABELS[4:]:
            if key == "review":
                state = "done" if inferred[key] else "running" if clip["status"] == "ready_for_review" else "waiting"
                parts.append(_step(label, state))
            else:
                parts.append(_step(label, *_stage_state(lookup.get((key, clip["idx"])), inferred[key])))
        html_rows += f'<div class=pipeline-label>Clip {clip["idx"] + 1}</div><div class="steps clip-steps">{"".join(parts)}</div>'
    return html_rows


def _dashboard_job(j):
    output = j.get("first_output")
    return {"id": str(j["id"]), "title": j["title"] or Path(j["source_path"] or j["source_url"] or "").name or str(j["id"])[:8],
            "status": j["status"], "error": j["error"], "created_at": j["created_at"].isoformat(),
            "style": (j["meta"] or {}).get("style", "viral"), "clips_requested": (j["meta"] or {}).get("clips"),
            "clip_count": j["n"], "ready_count": j["ready"], "duration": float(j["duration"] or 0),
            "cost_usd": float(j["usd"] or 0),
            "thumbnail": f"/thumbs/{j['id']}/{Path(output).name}" if output else None}


@app.get("/dashboard/jobs", dependencies=A)
def dashboard_jobs():
    jobs = db.q("""select j.*,
                  (select count(*) from clips c where c.job_id=j.id) as n,
                  (select count(*) from clips c where c.job_id=j.id and c.status='ready_for_review') as ready,
                  (select coalesce(sum((c.candidate->>'end')::numeric-(c.candidate->>'start')::numeric),0) from clips c where c.job_id=j.id) as duration,
                  (select output_path from clips c where c.job_id=j.id and c.output_path is not null order by idx limit 1) as first_output,
                  (select coalesce(sum(usd),0) from costs k where k.job_id=j.id) as usd
                  from jobs j order by j.created_at desc limit 100""")
    inbox = sorted(p.name for p in config.INBOX.iterdir() if p.suffix.lower() in ingest.VIDEO_EXT)
    return {"jobs": [_dashboard_job(j) for j in jobs], "inbox": inbox,
            "styles": [{"id": key, "label": value["label"]} for key, value in STYLES.items()]}


@app.get("/dashboard/jobs/{job_id}", dependencies=A)
def dashboard_job(job_id: uuid.UUID):
    j = _job(job_id)
    status = job_status(job_id)
    clips = db.q("select idx, status, review, review_note, candidate, edl, assets, qc, output_path, versions, ai_review, style, "
                 "render_engine, encoder, render_seconds, render_note, fail_kind from clips where job_id=%s order by idx", (job_id,))
    stages = db.q("select name, clip_idx, status, attempts, error, result, started_at, finished_at from stages where job_id=%s order by clip_idx, started_at nulls last", (job_id,))
    costs = db.q("""select coalesce(units->>'model','Unknown') as model, count(*) as calls,
                    coalesce(sum(coalesce((units->>'in')::bigint,0)+coalesce((units->>'out')::bigint,0)),0) as tokens,
                    coalesce(sum(usd),0) as usd from costs where job_id=%s group by 1 order by 1""", (job_id,))
    selected = {c["idx"] for c in clips}
    rejected = []
    analysis_dir = config.JOBS / str(job_id) / "analysis"
    for path in sorted(analysis_dir.glob("rank_*.json")) if analysis_dir.exists() else []:
        try:
            candidate = json.loads(path.read_text())
            if candidate.get("idx") not in selected:
                rejected.append({key: candidate.get(key) for key in ("idx", "quality", "why_engaging", "title")})
        except (OSError, ValueError, TypeError):
            continue
    clip_data = []
    for clip in clips:
        row = dict(clip)
        row["file"] = f"/files/{job_id}/{Path(clip['output_path']).name}" if clip["output_path"] else None
        row["thumbnail"] = f"/thumbs/{job_id}/{Path(clip['output_path']).name}" if clip["output_path"] else None
        row["file_bytes"] = _file_bytes(clip["output_path"])
        clip_data.append(row)
    source_path = j.get("source_path")
    source_available = bool(source_path and Path(source_path).is_file())
    output_dir = config.JOBS / str(job_id) / "output"
    zip_files = (sorted(output_dir.glob("clip_??.mp4")) + sorted(output_dir.glob("clip_??_assets.json"))) if output_dir.exists() else []
    download_bytes = sum(size for path in zip_files if (size := _file_bytes(str(path))) is not None)
    return {"job": {"id": str(job_id), "title": j["title"] or f"Job {str(job_id)[:8]}", "status": j["status"],
                    "error": j["error"], "created_at": j["created_at"].isoformat(), "style": (j["meta"] or {}).get("style", "viral"),
                    "elapsed_s": status["elapsed_s"], "cost_usd": status["cost_usd"], "download_bytes": download_bytes, "source_available": source_available,
                    "burned_captions": (j["meta"] or {}).get("burned_captions"),
                    "root_cause": _root_cause(str(job_id)) if j["status"] in ("failed", "done", "no_highlights") else None},
            "clips": clip_data, "stages": stages, "costs": costs, "rejected": rejected}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    jobs = db.q("""select j.id, j.title, j.source_type, j.source_url, j.source_path, j.status, j.error, j.created_at, j.meta,
                  (select count(*) from clips c where c.job_id=j.id) as n,
                  (select count(*) from clips c where c.job_id=j.id and c.status='ready_for_review') as ready,
                  (select coalesce(sum((c.candidate->>'end')::numeric-(c.candidate->>'start')::numeric),0) from clips c where c.job_id=j.id) as duration,
                  (select output_path from clips c where c.job_id=j.id and c.output_path is not null order by idx limit 1) as first_output,
                  (select coalesce(sum(usd),0) from costs k where k.job_id=j.id) as usd
                  from jobs j order by j.created_at desc limit 50""")
    rows = ""
    for j in jobs:
        title = j['title'] or Path(j['source_path'] or j['source_url'] or '').name or str(j['id'])[:8]
        if j["first_output"]:
            name = Path(j["first_output"]).name
            thumb = f'<img class=history-thumb src="/thumbs/{j["id"]}/{e(name)}" alt="">'
        else:
            thumb = '<span class=history-thumb></span>'
        rows += f"""<tr><td class=history-title><a href="/jobs/{j['id']}/view">{thumb}</a><a href="/jobs/{j['id']}/view"><b>{e(title)}</b><br><span class=muted>{e(STYLES.get((j['meta'] or {}).get('style', 'viral'), STYLES['viral'])['label'])}</span></a></td>
          <td data-label=Submitted>{j['created_at']:%d %b %H:%M}</td><td data-label=Status><span class="pill {e(j['status'])}">{e(j['status'].replace('_', ' '))}</span></td>
          <td data-label=Clips>{j['n']}</td><td data-label=Duration>{_clock(j['duration'])}</td><td data-label="AI cost">${float(j['usd']):.4f}</td></tr>"""
    rows = rows or "<tr><td colspan=6 class=muted>No jobs yet.</td></tr>"
    inbox = sorted(p.name for p in config.INBOX.iterdir() if p.suffix.lower() in ingest.VIDEO_EXT)
    return page("Gaming Clipper", f"""<h1>Gaming Clipper</h1>
<div class=card><form class=sub method=post action="/ui/submit" enctype="multipart/form-data">
 <label class=full>YouTube URL (your own or CC-licensed video)<input name=url placeholder="https://www.youtube.com/watch?v=..."></label>
 <label>…or upload a file<input type=file name=file accept="video/*"></label>
 <label>…or pick from inbox/<select name=inbox><option value="">—</option>{''.join(f'<option>{e(n)}</option>' for n in inbox)}</select></label>
 <label>Editing style{style_select()}</label><label>Title (optional)<input name=title></label>
 <label>Number of clips<select name=clips><option value="">Best only (AI decides)</option><option value=1>1</option><option value=2>2</option><option value=3>3</option><option value=4>4</option><option value=5>5</option><option value=6>6</option><option value=7>7</option><option value=8>8</option></select></label>
 <label class=full><input type=checkbox name=rights value=yes required style="width:auto"> I own this video or have the rights to edit it</label>
 <div class=full><button>Generate clips</button> <span class=muted>Clips appear here and in Telegram. Nothing is published.</span></div>
</form></div>
<div class="card how"><h2 style="margin-top:0">How it works</h2><p>Upload a recording and the pipeline downloads it, analyses speech, sound and scene changes, then AI finds and scores the strongest moments. The best moments get an edit plan, visual assets, a vertical render and automated quality checks before you review them.</p>{_static_steps()}</div>
<h1 style="margin-top:28px">History</h1><div class=card><table class=history><thead><tr><th>Video</th><th>Submitted</th><th>Status</th><th>Clips</th><th>Duration</th><th>AI cost</th></tr></thead><tbody>{rows}</tbody></table></div>""")


@app.post("/ui/submit")
async def ui_submit(request: Request, url: str = Form(""), inbox: str = Form(""), style: str = Form("viral"),
                    title: str = Form(""), rights: str = Form(""), clips: str = Form(""), file: UploadFile | None = None):
    _same_origin(request)
    if rights != "yes":
        raise HTTPException(400, "Please confirm you have the rights to edit this video.")
    payload = {"rights_confirmed": True, "style": style if style in STYLES else "viral", "title": title or None,
               "clips": int(clips) if clips.isdigit() and 1 <= int(clips) <= 8 else None}
    if file is not None and file.filename:
        name = Path(file.filename).name
        if Path(name).suffix.lower() not in ingest.VIDEO_EXT:
            raise HTTPException(400, f"Unsupported file type {Path(name).suffix}. Use mp4, mov, mkv, webm or m4v.")
        with (config.INBOX / name).open("wb") as f:
            shutil.copyfileobj(file.file, f, 8 << 20)
        payload.update(source_type="file", filename=name)
    elif inbox:
        payload.update(source_type="file", filename=Path(inbox).name)
    elif url.strip():
        if ingest.youtube_id(url):
            payload.update(source_type="youtube", url=url.strip())
        elif ingest.twitch_id(url):
            payload.update(source_type="twitch", url=url.strip())
        else:
            raise HTTPException(400, "That is not a YouTube video or finished Twitch VOD link.")
    else:
        raise HTTPException(400, "Give a YouTube or Twitch VOD URL, upload a file, or pick one from inbox/.")
    from .main import JobIn, create_job
    out = create_job(JobIn(**payload))
    return RedirectResponse(f"/jobs/{out['job_id']}/view", status_code=303)


def _root_cause(job_id: str):
    from .pipeline import _first_error
    return _first_error(job_id)


def _thumb(job_id, name) -> Path:
    out = config.JOBS / str(job_id) / "output"
    src = out / Path(name).name
    th = out / (src.stem + ".jpg")
    if src.is_file() and (not th.exists() or th.stat().st_mtime < src.stat().st_mtime):
        media.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.2", "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", str(th)], 60)
    return th


@app.get("/thumbs/{job_id}/{name}")
def thumb(job_id: uuid.UUID, name: str):
    th = _thumb(job_id, name)
    if not th.is_file():
        raise HTTPException(404)
    return FileResponse(th)


@app.get("/jobs/{job_id}/zip")
def zip_all(job_id: uuid.UUID):
    out = config.JOBS / str(job_id) / "output"
    files = sorted(p for p in out.glob("clip_??.mp4")) + sorted(out.glob("clip_??_assets.json")) if out.exists() else []
    if not files:
        raise HTTPException(404, "no clips yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(f, f.name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="clips_{str(job_id)[:8]}.zip"'})


def regenerate(job_id: str, idx: int, style: str | None, opts: dict | None = None):
    c = _clip(job_id, idx)
    if c["status"] in ("rendering",):
        raise HTTPException(409, "clip is rendering; wait for it to finish")
    out = Path(c["output_path"]) if c["output_path"] else None
    versions = list(c["versions"] or [])
    if out and out.is_file():  # keep the previous cut for comparison
        v = out.with_name(f"{out.stem}_v{len(versions) + 1}.mp4")
        shutil.copy2(out, v)
        versions.append({"file": v.name, "style": (c["edl"] or {}).get("style"), "qc_passed": (c["qc"] or {}).get("passed"),
                         "review": c["review"], "ts": time.time()})
    last = config.job_dir(job_id) / "analysis" / f"edl_{idx}_last_error.json"
    last.unlink(missing_ok=True)
    if opts and (opts.get("mode") or opts.get("edit")):  # another mode / amount of editing for this clip only
        from . import modes
        jm = (db.q1("select meta from jobs where id=%s", (job_id,)) or {}).get("meta") or {}
        n = modes.normalize({"mode": opts.get("mode") or jm.get("mode"), "edit": opts.get("edit"), "content_type": jm.get("content_type"),
                             "instructions": jm.get("instructions"), "caption_look": jm.get("caption_look")})
        n["style"] = style = modes.style_for(n["mode"], n["edit"])
        cand = dict(c["candidate"] or {})
        cand["opts"] = n
        db.q("update clips set candidate=%s where job_id=%s and idx=%s", (db.J(cand), job_id, idx))
    db.q("update clips set style=%s, status='selected', review='pending', versions=%s where job_id=%s and idx=%s",
         (style if style in STYLES else c["style"], db.J(versions), job_id, idx))
    db.event(job_id, "info", "regenerate", f"clip {idx} regenerate requested", {"style": style, "version": len(versions)})
    from .queue import enqueue
    enqueue(job_id, "direct", idx, force=True)  # director -> assets -> render -> qc -> review, all in the worker
    return {"idx": idx, "status": "regenerating", "style": style}


@app.post("/jobs/{job_id}/clips/{idx}/regenerate")
def api_regenerate(job_id: uuid.UUID, idx: int, request: Request, style: str | None = None, mode: str | None = None, edit: str | None = None):
    from .main import auth
    auth(request.headers.get("x-clipper-token", ""))
    return regenerate(str(job_id), idx, style, {"mode": mode, "edit": edit})


@app.post("/ui/regenerate/{job_id}/{idx}")
def ui_regenerate(job_id: uuid.UUID, idx: int, request: Request, style: str = Form("viral")):
    _same_origin(request)
    regenerate(str(job_id), idx, style)
    return RedirectResponse(f"/jobs/{job_id}/view", status_code=303)


@app.get("/jobs/{job_id}/view", response_class=HTMLResponse)
def view(job_id: uuid.UUID):
    s = job_status(job_id)
    j = _job(job_id)
    style = (j["meta"] or {}).get("style", "viral")
    busy = s["busy"] or s["status"] in ("created", "ingested", "analyzed", "ranking", "selected")
    clip_rows = db.q("select idx, style, versions, candidate, edl, assets, qc, status, review, output_path from clips where job_id=%s order by idx", (job_id,))
    rows = {c["idx"]: c for c in clip_rows}
    stage_rows = db.q("select name, clip_idx, status, error, result, started_at, finished_at from stages where job_id=%s", (job_id,))
    cards = []
    for c in s["clips"]:
        r = rows[c["idx"]]
        cand, edl, qc_data = r["candidate"] or {}, r["edl"] or {}, r["qc"] or {}
        name = Path(c["local_path"]).name if c["local_path"] else None
        poster = f' poster="/thumbs/{job_id}/{name}"' if name else ""
        video = f'<video src="/files/{job_id}/{name}?v={int(time.time())}" controls preload=none{poster}></video>' if name else '<div class=muted style="aspect-ratio:9/16;display:grid;place-items:center;border:1px dashed var(--line);border-radius:10px">rendering…</div>'
        sg = _sig(job_id, c["idx"])
        review = (f'<form method=post action="/ui/review/{job_id}/{c["idx"]}/approved/{sg}"><button>✅ Approve</button></form>'
                  f'<form method=post action="/ui/review/{job_id}/{c["idx"]}/rejected/{sg}"><button class=ghost>❌ Reject</button></form>') if c["status"] == "ready_for_review" else ""
        regen = (f'<form method=post action="/ui/regenerate/{job_id}/{c["idx"]}">{style_select(current=(r["edl"] or {}).get("style") or style)}'
                 f'<button class=ghost>↻ Regenerate</button></form>')
        checks = qc_data.get("checks") or {}
        qc_items = "".join(f'<li class="{"ok" if value.get("ok") else "bad"}">{"✓" if value.get("ok") else "✕"} {e(str(key).replace("_", " ").title())}: {e(str(value.get("detail", "")))}</li>' for key, value in checks.items())
        qc = f'<ul class=qc-list>{qc_items}</ul>' if qc_items else '<span class=muted>Checks have not run yet.</span>'
        vers = "".join(f'<a href="/files/{job_id}/{e(v["file"])}">v{i + 1} ({e(v.get("style") or "?")})</a> ' for i, v in enumerate(r["versions"] or []))
        hook = edl.get("hook", {}).get("text") if isinstance(edl.get("hook"), dict) else None
        hook = hook or cand.get("hook") or "—"
        moment_type = edl.get("moment_type") or cand.get("event") or "—"
        below = '<p><span class=quality-badge>Below quality bar (added because you asked for N clips)</span></p>' if cand.get("below_quality_bar") else ""
        cards.append(f"""<div class=card>{video}<h3 style="margin:10px 0 4px">{e(c['title'] or 'Untitled')}</h3>
<p class=muted style="margin:0 0 8px">{e(c['description'] or '')}</p>
<div class=why><h4>Why this clip</h4>{below}<dl><dt>AI score</dt><dd><b>{c['quality']:.1f}/10</b></dd><dt>Moment type</dt><dd>{e(str(moment_type))}</dd><dt>Source</dt><dd>{_clock(c['source_start'])}–{_clock(c['source_end'])} of the original</dd><dt>Hook</dt><dd>“{e(str(hook))}”</dd></dl></div>
<h4>Quality checks</h4>{qc}<dl><dt>Status</dt><dd><span class="pill {e(c['status'])}">{e(c['status'].replace('_', ' '))}</span> · review: <b>{e(c['review'])}</b></dd><dt>Edit</dt><dd>{e(edl.get('style', '-'))} · {len(edl.get('zooms', []))} zooms · {len(edl.get('sfx', []))} sfx · {len(edl.get('overlays', []))} overlays</dd>{f'<dt>Versions</dt><dd>{vers}</dd>' if vers else ''}</dl>
<div class=row style="margin-top:10px">{f'<a href="/files/{job_id}/{name}" download><button class=ghost>⬇ Download</button></a>' if name else ''}{review}</div>
<div class=row style="margin-top:8px">{regen}</div></div>""")
    err = "".join(f'<p class=bad>⚠ {e(f["stage"])}: {e((f["error"] or "")[:300])}</p>' for f in s["failed"]) + (f'<p class=bad>⚠ {e(s["error"])}</p>' if s["error"] else "")
    selected = set(rows)
    rejected = []
    analysis_dir = config.JOBS / str(job_id) / "analysis"
    for path in sorted(analysis_dir.glob("rank_*.json")) if analysis_dir.exists() else []:
        try:
            candidate = json.loads(path.read_text())
            if candidate.get("idx") not in selected:
                rejected.append(f'<div class=reject-item><b>{e(str(candidate.get("title") or "Untitled moment"))}</b> · {float(candidate.get("quality", 0)):.1f}/10<br><span class=muted>{e(str(candidate.get("why_engaging") or "No reason provided."))}</span></div>')
        except (OSError, ValueError, TypeError):
            continue
    rejected_html = f'<details class="card rejects"><summary>Rejected moments ({len(rejected)})</summary>{"".join(rejected)}</details>' if rejected else ""
    costs = db.q("""select coalesce(units->>'model','Unknown') as model, count(*) as calls,
                    coalesce(sum(coalesce((units->>'in')::bigint,0)+coalesce((units->>'out')::bigint,0)),0) as tokens,
                    coalesce(sum(usd),0) as usd from costs where job_id=%s group by 1 order by 1""", (job_id,))
    cost_rows = "".join(f'<tr><td>{e(x["model"])}</td><td>{x["calls"]}</td><td>{int(x["tokens"]):,}</td><td>${float(x["usd"]):.4f}</td></tr>' for x in costs) or '<tr><td colspan=4 class=muted>No AI calls recorded yet.</td></tr>'
    costs_html = f'<div class="card costs"><h2 style="margin-top:0">AI cost</h2><table><tr><th>Model</th><th>Calls</th><th>Tokens</th><th>USD</th></tr>{cost_rows}</table><p class=muted>Free tier: actual charge may be $0.</p></div>'
    head = f"""<h1>{e(j['title'] or 'Job ' + str(job_id)[:8])}</h1>
<div class=card><div class=row><span class="pill {e(s['status'])}">{e(s['status'].replace('_', ' '))}</span><span>{e(STYLES.get(style, STYLES['viral'])['label'])}</span>
<span class=muted>{(s['elapsed_s'] or 0):.0f}s · AI cost ${s['cost_usd']:.4f} · {len(s['clips'])} clip(s)</span>
{f'<a href="/jobs/{job_id}/zip"><button class=ghost>⬇ Download all</button></a>' if any(c["local_path"] for c in s["clips"]) else ''}</div>
{err}{'<p class=muted>Working… this page refreshes every 10s.</p>' if busy else ''}<h2>Pipeline</h2>{_pipeline(stage_rows, clip_rows, s['status'])}</div>"""
    clips_html = f'<div class=grid style="margin-top:16px">{"".join(cards)}</div>' if cards else ("" if busy else "<p>No clips: no moment was strong enough (quality over quantity).</p>")
    body = head + clips_html + rejected_html + costs_html
    return page("Clipper job", body, refresh=busy)
