"""Telegram notifications sent straight from the worker (no n8n). Optional: needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."""
import html
import os
from pathlib import Path

import httpx

from . import db

API = "https://api.telegram.org/bot{token}/{method}"
MAX_VIDEO = 49 * 1024 * 1024  # Bot API upload limit is 50 MB


def enabled() -> bool:
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        return False
    try:
        from .manage import load_settings
        return load_settings().telegram_notify
    except Exception:  # noqa: BLE001
        return True


def dashboard_url() -> str:
    url = os.environ.get("DASHBOARD_URL", "").rstrip("/")
    if not url and os.environ.get("RUNPOD_POD_ID"):
        url = f"https://{os.environ['RUNPOD_POD_ID']}-8787.proxy.runpod.net"
    return url


def _call(method: str, **kw):
    r = httpx.post(API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method), timeout=120, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"Telegram {method}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def send(text: str):
    if enabled():
        _call("sendMessage", json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:4000], "parse_mode": "HTML",
                                   "disable_web_page_preview": True})


def job_finished(job_id: str):
    if not enabled():
        return
    j = db.q1("select id, status, error, title, meta from jobs where id=%s", (job_id,))
    clips = db.q("select idx, status, candidate, edl, output_path, encoder, render_seconds, fail_kind from clips where job_id=%s order by idx", (job_id,))
    meta = j["meta"] or {}
    secs = (meta.get("finished_at") or 0) - (meta.get("t0") or 0)
    cost = db.q1("select coalesce(sum(usd),0) as usd from costs where job_id=%s", (job_id,))["usd"]
    ok = [c for c in clips if c["status"] == "ready_for_review"]
    e = html.escape
    lines = [f"{'✅' if ok else '⚠️'} <b>Clip job {str(job_id)[:8]}</b> — {e(j['status'])}",
             f"{len(ok)} clip(s) ready, {len(clips) - len(ok)} not ready · {round(secs)}s · AI ${float(cost):.4f}"]
    if not ok and j["error"]:
        lines.append(e(j["error"][:600]))
    for c in clips:
        title = (c["edl"] or {}).get("title") or (c["candidate"] or {}).get("title") or f"clip {c['idx']}"
        extra = f" · {e(c['encoder'])} {c['render_seconds']}s" if c["encoder"] and c["render_seconds"] else ""
        bad = f" [{e(c['fail_kind'])}]" if c["fail_kind"] else ""
        lines.append(f"• {e(title)} · {e(c['status'])}{extra}{bad}")
    url = dashboard_url()
    if url:
        lines.append(f"\n{url}/jobs/{job_id}")
    send("\n".join(lines))
    for c in ok:
        p = Path(c["output_path"] or "")
        if p.is_file() and p.stat().st_size <= MAX_VIDEO:
            title = (c["edl"] or {}).get("title") or (c["candidate"] or {}).get("title") or ""
            with p.open("rb") as f:
                _call("sendVideo", data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": title[:900], "supports_streaming": "true"},
                      files={"video": (p.name, f, "video/mp4")})
