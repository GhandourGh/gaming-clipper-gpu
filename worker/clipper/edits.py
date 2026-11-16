"""Quick edits on a finished clip, in the app instead of downloading it and editing it somewhere else.

  GET  /jobs/{id}/clips/{idx}/text   what the clip shows now: hook card text, header text, caption size and position
  POST /jobs/{id}/clips/{idx}/text   change any of them: the clip is re-rendered (about 30 s on the GPU). The plan is NOT redone
                                     and the AI review is skipped, so only what you typed changes. The previous cut is kept
                                     as a version, like Regenerate does.
Works for the GTA VI layouts (gta_cam, gta_hook), which draw their text in layouts.py."""
import shutil
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import db, layouts, queue, render
from .gemini import clip_meta
from .main import A, app

SIZES = ("small", "medium", "large")


class TextEditIn(BaseModel):
    hook_text: str | None = Field(None, max_length=80)      # "" removes the card
    header_text: str | None = Field(None, max_length=40)    # "" shows only the logo
    caption_size: str | None = Field(None, pattern="^(small|medium|large)$")
    caption_shift: int | None = Field(None, ge=-300, le=300)  # px: positive moves the captions down


def _clip(job_id, idx: int) -> dict:
    c = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not c:
        raise HTTPException(404, "clip not found")
    return c


def _current(job_id, idx: int) -> dict:
    m = clip_meta(str(job_id), idx)
    return {"template": m.get("template"), "editable": m.get("template") in layouts.KINDS,
            "hook_text": m.get("hook_text") or "", "header_text": m.get("header_text") or "",
            "caption_size": m.get("caption_size") or "medium", "caption_shift": int(m.get("caption_shift") or 0)}


@app.get("/jobs/{job_id}/clips/{idx}/text", dependencies=A)
def get_text(job_id: str, idx: int):
    _clip(job_id, idx)
    return _current(job_id, idx)


@app.post("/jobs/{job_id}/clips/{idx}/text", dependencies=A)
def edit_text(job_id: str, idx: int, b: TextEditIn):
    c = _clip(job_id, idx)
    cur = _current(job_id, idx)
    if not cur["editable"]:
        raise HTTPException(409, "text editing works for the GTA VI layouts (framed cam, widescreen + hook card)")
    if c["status"] not in render.RENDERABLE or not c["edl"] or c["status"] == "rendering":
        raise HTTPException(409, f"the clip cannot be edited right now (status {c['status']})")
    if db.q1("select 1 from stages where job_id=%s and clip_idx=%s and status in ('queued','running') limit 1", (job_id, idx)):
        raise HTTPException(409, "this clip is still working: wait for it to finish")
    changes = {k: v for k, v in b.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(400, "nothing to change")
    versions = list(c["versions"] or [])
    out = Path(c["output_path"]) if c["output_path"] else None
    if out and out.is_file():  # keep the previous cut
        v = out.with_name(f"{out.stem}_v{len(versions) + 1}.mp4")
        shutil.copy2(out, v)
        versions.append({"file": v.name, "style": (c["edl"] or {}).get("style"), "qc_passed": (c["qc"] or {}).get("passed"),
                         "review": c["review"], "ts": time.time(), "note": "before a text edit"})
    cand = dict(c["candidate"] or {})
    opts = dict(cand.get("opts") or {})
    opts.update(changes)
    opts["no_review"] = True  # a text edit is not re-judged (and cannot trigger a redo that would re-plan the clip)
    cand["opts"] = opts
    db.q("update clips set candidate=%s, versions=%s, status='assets_ready' where job_id=%s and idx=%s",
         (db.J(cand), db.J(versions), job_id, idx))
    db.event(job_id, "info", "edit", f"clip {idx}: text edited ({', '.join(changes)})", {})
    queue.enqueue(job_id, "render", idx, force=True)
    return {"idx": idx, "status": "rendering", "changed": sorted(changes)}
