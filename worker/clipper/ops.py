"""Operations: readiness preflight, GPU telemetry, admin endpoints, scheduled housekeeping and the Pod's idle auto-stop.

/ready proves the GPU path instead of assuming it: it runs a real 1-second h264_nvenc encode, checks libass/drawtext
filters, reads nvidia-smi and reports which device Whisper actually loaded on."""
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel

from . import config, db, media, queue
from .main import A, app

STATE = Path(os.environ.get("CLIPPER_STATE", "/workspace/clipper-runtime"))
ENV_FILE = Path(os.environ.get("CLIPPER_ENV_FILE", "/workspace/clipper.env"))
IDLE_STOP_MIN = float(os.environ.get("IDLE_STOP_MIN", "30"))  # 0 = never stop automatically
STARTED = time.time()
_last_activity = time.time()
_ready_cache: dict = {"t": 0.0, "v": None}
ENV_KEYS = {"GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENROUTER_API_KEY", "GROQ_API_KEY",
            "FREESOUND_API_KEY", "JAMENDO_CLIENT_ID", "SHORTSINTEL_API_KEY", "TRENDSMCP_API_KEY", "AYRSHARE_API_KEY",
            "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "DASHBOARD_PASSWORD", "DASHBOARD_URL", "IDLE_STOP_MIN",
            "GEMINI_FREE_TIER", "BUDGET_SINCE", "BUDGET_JOB_USD", "BUDGET_DAY_USD", "BUDGET_TOTAL_USD", "WHISPER_MODEL",
            "STORY_WHISPER_MODEL", "LLM_PARALLEL", "LLM_THREADS", "GPU_THREADS", "QC_THREADS", "INGEST_THREADS",
            "LIVE_CHUNK_S", "RETENTION_DAYS", "GPU_USD_PER_HOUR", "MAX_REPLACEMENT_GPU_USD_PER_HOUR"}


@app.middleware("http")
async def _activity(request: Request, call_next):
    global _last_activity
    # Dashboard GET polling is observation, not activity. Counting it kept an idle
    # GPU alive forever while a browser tab was open. Mutations represent real use;
    # completed stages are accounted for separately by idle_s().
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        _last_activity = time.time()
    return await call_next(request)


def _sh(cmd: list[str], timeout=20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def gpu() -> dict:
    code, out = _sh(["nvidia-smi", "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,"
                     "encoder.stats.sessionCount,temperature.gpu,driver_version", "--format=csv,noheader,nounits"], 10)
    if code != 0:
        return {"present": False, "detail": out.strip()[:200]}
    f = [x.strip() for x in out.strip().splitlines()[0].split(",")]
    num = lambda x: float(x) if re.match(r"^[\d.]+$", x) else None  # noqa: E731
    return {"present": True, "name": f[0], "util_pct": num(f[1]), "mem_util_pct": num(f[2]), "mem_used_mb": num(f[3]),
            "mem_total_mb": num(f[4]), "encoder_sessions": num(f[5]), "temp_c": num(f[6]), "driver": f[7]}


def _ffmpeg_checks() -> dict:
    ff = ffmpeg_bin()
    out = {"path": ff}
    code, ver = _sh([ff, "-hide_banner", "-version"], 10)
    out["version"] = ver.splitlines()[0][:120] if code == 0 else f"missing: {ver[:120]}"
    _, enc = _sh([ff, "-hide_banner", "-encoders"], 10)
    _, flt = _sh([ff, "-hide_banner", "-filters"], 10)
    out["nvenc_listed"] = "h264_nvenc" in enc
    out["libass"] = bool(re.search(r"^\s*\S+\s+ass\s", flt, re.M)) and bool(re.search(r"\bsubtitles\b", flt))
    out["drawtext"] = "drawtext" in flt
    if media.NVENC:
        t0 = time.time()
        code, log = _sh([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30",
                         "-t", "1", *media.h264("master"), "-f", "null", "-"], 60)
        out["nvenc_test_encode"] = code == 0
        out["nvenc_test_seconds"] = round(time.time() - t0, 2)
        if code != 0:
            out["nvenc_error"] = log.strip()[-300:]
    fr = Path(os.environ.get("FFMPEG_FULL", ff))
    out["fastrender_ffmpeg"] = str(fr) if fr.exists() or shutil.which(str(fr)) else f"missing: {fr}"
    return out


def readiness(refresh: bool = False) -> dict:
    if not refresh and _ready_cache["v"] and time.time() - _ready_cache["t"] < 300:
        v = dict(_ready_cache["v"])
        v.update(gpu=gpu(), runtime=runtime())
        return v
    checks = {}
    try:
        db.q1("select 1")
        checks["postgres"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        checks["postgres"] = {"ok": False, "detail": str(e)[:200]}
    ff = _ffmpeg_checks()
    checks["ffmpeg"] = {**ff, "ok": bool(ff.get("nvenc_test_encode")) if media.NVENC else "missing" not in ff["version"]}
    from . import analysis
    try:
        import ctranslate2
        cuda_n = ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001
        cuda_n = None
    checks["whisper"] = {"ok": not media.LINUX or bool(cuda_n), "cuda_devices": cuda_n, "model": config.WHISPER_MODEL,
                         "loaded": dict(analysis.WHISPER_DEVICE)}
    checks["llm"] = {"ok": bool(os.environ.get("GEMINI_API_KEY")), "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
                     "openrouter_key": bool(os.environ.get("OPENROUTER_API_KEY")), "groq_key": bool(os.environ.get("GROQ_API_KEY"))}
    from . import notify
    checks["telegram"] = {"ok": True, "enabled": notify.enabled()}
    checks["render"] = {"ok": True, "engine": "ffmpeg " + ("h264_nvenc (GPU)" if media.NVENC else "VideoToolbox/libx264"),
                        "cpu_fallback_allowed": config.RENDER_CPU_FALLBACK}
    du = shutil.disk_usage(config.DATA)
    pct = round(du.used / du.total * 100, 1)
    checks["disk"] = {"ok": pct < 95, "used_pct": pct, "free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1),
                      "level": "critical" if pct >= 95 else "high" if pct >= 85 else "warn" if pct >= 70 else "ok"}
    must = ["postgres", "ffmpeg", "llm", "disk"] + (["whisper"] if media.LINUX else [])
    v = {"ready": all(checks[k]["ok"] for k in must), "checks": checks}
    _ready_cache.update(t=time.time(), v=v)
    v = dict(v)
    v.update(gpu=gpu(), runtime=runtime())
    return v


def idle_s() -> float:
    """Seconds since the last dashboard/API use or the last finished stage, whichever is later."""
    try:
        last_stage = float(db.q1("select extract(epoch from max(finished_at)) as t from stages")["t"] or 0)
    except Exception:  # noqa: BLE001
        last_stage = 0.0
    return time.time() - max(_last_activity, last_stage)


def runtime() -> dict:
    idle = idle_s()
    rate = float(os.environ.get("GPU_USD_PER_HOUR", "0") or 0)
    up = time.time() - STARTED
    return {"pod_id": os.environ.get("RUNPOD_POD_ID"), "uptime_s": round(up), "idle_s": round(idle),
            "idle_stop_min": IDLE_STOP_MIN, "stop_in_s": round(max(0, IDLE_STOP_MIN * 60 - idle)) if IDLE_STOP_MIN else None,
            "queue": queue.qsize(), "queue_stats": queue.stats(), "busy": queue.busy(), "gpu_usd_per_hour": rate or None,
            "replacement_gpu_ceiling_usd": float(os.environ.get("MAX_REPLACEMENT_GPU_USD_PER_HOUR", "0.20")),
            "session_cost_usd": round(rate * up / 3600, 3) if rate else None, "git_rev": _git_rev()}


def _git_rev() -> str | None:
    code, out = _sh(["git", "-C", str(config.ROOT), "rev-parse", "--short", "HEAD"], 5)
    return out.strip() if code == 0 else None


@app.get("/ready")
def ready(refresh: bool = False):
    """Public, secret-free readiness summary (Runpod/health probes). Details need the token: /dashboard/gpu."""
    v = readiness(refresh)
    return {"ready": v["ready"], "failing": [k for k, c in v["checks"].items() if not c.get("ok")]}


@app.get("/dashboard/gpu", dependencies=A)
def dashboard_gpu(refresh: bool = False):
    return readiness(refresh)


class EnvIn(BaseModel):
    values: dict[str, str]


@app.post("/admin/env", dependencies=A)
def admin_env(b: EnvIn):
    """Store allow-listed settings/keys in the Pod's env file and apply them now. Values are never returned."""
    bad = sorted(set(b.values) - ENV_KEYS)
    if bad:
        raise HTTPException(400, f"not allowed: {', '.join(bad)}")
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    keep = [ln for ln in lines if ln.split("=", 1)[0].strip() not in b.values]
    for k, val in b.values.items():
        if "\n" in val:
            raise HTTPException(400, f"{k}: newline in value")
        keep.append(f"{k}={val}")
        os.environ[k] = val
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(keep) + "\n")
    ENV_FILE.chmod(0o600)
    _ready_cache["v"] = None
    return {"stored": sorted(b.values)}


@app.post("/admin/update", dependencies=A)
def admin_update():
    """Pull the latest code from GitHub, rebuild what changed and restart worker + dashboard (supervisors restart them)."""
    script = config.ROOT / "scripts" / "pod-update.sh"
    if not script.exists():
        raise HTTPException(404, "pod-update.sh missing")
    STATE.mkdir(parents=True, exist_ok=True)
    log = (STATE / "update.log").open("a")
    subprocess.Popen(["bash", str(script)], stdout=log, stderr=log, start_new_session=True, cwd=str(config.ROOT))
    return {"started": True, "log": str(STATE / "update.log")}


@app.get("/admin/logs", dependencies=A)
def admin_logs(name: str = Query("worker", pattern="^(worker|dashboard|boot|update|postgres)$"), lines: int = Query(200, ge=1, le=2000)):
    p = STATE / f"{name}.log"
    if not p.exists():
        raise HTTPException(404, f"{p} not found")
    with p.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        text = f.read().decode("utf-8", "replace")
    return {"name": name, "lines": text.splitlines()[-lines:]}


# ---------------- housekeeping + idle stop ----------------

def _live_blocks_stop() -> str | None:
    from . import live
    try:
        st = live.status()
    except Exception:  # noqa: BLE001
        return None
    if any(s.get("recording") for s in st):
        return "recording a live stream"
    if live.ENABLED and any(s.get("enabled", True) for s in st):
        return "live channels are being watched (disable them on the Live page to allow auto-stop)"
    return None


def stop_pod(reason: str) -> dict:
    pod = os.environ.get("RUNPOD_POD_ID")
    if not pod:
        return {"stopped": False, "detail": "not running on a Runpod Pod"}
    db.event(None, "warn", "idle-stop", f"stopping Pod {pod}: {reason}", {})
    if os.environ.get("CLIPPER_DB_BACKUP") == "1":  # database on the container disk: save it to the volume first
        _sh(["bash", str(config.ROOT / "scripts" / "db-backup.sh")], 300)
    try:
        from . import notify
        notify.send(f"⏹ GPU Pod stopping: {reason}. Start it again in the Runpod console when you need it.")
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("runpodctl"):
        code, out = _sh(["runpodctl", "pod", "stop", pod], 60)
        if code == 0:
            return {"stopped": True, "via": "runpodctl"}
    key = os.environ.get("RUNPOD_API_KEY")
    if key:
        r = httpx.post(f"https://rest.runpod.io/v1/pods/{pod}/stop", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        return {"stopped": r.status_code < 300, "via": "rest", "status": r.status_code}
    return {"stopped": False, "detail": "no runpodctl and no RUNPOD_API_KEY in the Pod"}


@app.post("/admin/stop-pod", dependencies=A)
def admin_stop_pod():
    if queue.busy():
        raise HTTPException(409, "jobs are still running; wait or cancel them first")
    return stop_pod("stopped from the dashboard")


def _housekeeping():
    last_cleanup = last_watch = 0.0
    while True:
        time.sleep(60)
        now = time.time()
        try:
            if now - last_cleanup > 24 * 3600:
                last_cleanup = now
                from .main import cleanup
                cleanup()
            from .manage import load_settings
            if load_settings().watch_enabled and now - last_watch > 30 * 60:
                last_watch = now
                from .main import watch_check
                res = watch_check()
                new = res.get("new") or []
                if new:
                    from . import notify
                    notify.send("📺 New videos on watched channels:\n" + "\n".join(
                        f"• {v.get('title', '')} {v.get('url', '')}" for v in new[:10]))
            if IDLE_STOP_MIN > 0 and os.environ.get("RUNPOD_POD_ID"):
                idle = idle_s()
                if idle > IDLE_STOP_MIN * 60 and not queue.busy() and not _live_blocks_stop():
                    stop_pod(f"idle for {round(idle / 60)} min with no jobs")
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()


def mark_activity():
    global _last_activity
    _last_activity = time.time()


def start():
    def _warm():  # load Whisper on the GPU now so the first job does not pay for it
        try:
            if media.LINUX:
                from . import analysis
                analysis._faster_model(config.WHISPER_MODEL)
            readiness(refresh=True)
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
    threading.Thread(target=_warm, daemon=True, name="clipper-warmup").start()
    threading.Thread(target=_housekeeping, daemon=True, name="clipper-housekeeping").start()
