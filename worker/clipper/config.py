import os
import platform
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
INBOX = ROOT / "inbox"
JOBS = DATA / "jobs"
ASSET_CACHE = DATA / "asset_cache"
RENDERER = ROOT / "renderer"
for p in (DATA, INBOX, JOBS, ASSET_CACHE):
    p.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("CLIPPER_TOKEN", "")
PORT = int(os.environ.get("CLIPPER_PORT", "8787"))
N8N_HEALTH_URL = os.environ.get("N8N_HEALTH_URL", "http://127.0.0.1:5678/healthz")
DB_DSN = os.environ.get(
    "CLIPPER_DB_DSN",
    f"postgresql://clipper:{os.environ.get('CLIPPER_DB_PASSWORD', '')}@127.0.0.1:5435/clipper",
)
_MAC = platform.system() == "Darwin"
# A failed fast (GPU) render may fall back to Remotion (CPU, Chrome, ~20x slower). On Linux GPU workers that is OFF by
# default: the clip fails as render_failed with the real error instead of silently burning minutes on the CPU.
RENDER_CPU_FALLBACK = os.environ.get("RENDER_CPU_FALLBACK", "0" if platform.system() == "Linux" else "1") == "1"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo" if _MAC else "large-v3-turbo")
# Full multi-hour story indexing values coverage and speed; selected beats are re-transcribed with WHISPER_MODEL.
STORY_WHISPER_MODEL = os.environ.get("STORY_WHISPER_MODEL", "mlx-community/whisper-base-mlx" if _MAC else "base")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
STREAM_SECTION_S = float(os.environ.get("STREAM_SECTION_S", "2400"))  # long YouTube videos: download only the best 40 min
FULL_TRANSCRIBE_MAX_S = float(os.environ.get("FULL_TRANSCRIBE_MAX_S", "2700"))  # longer sources: transcribe only the clip windows
def render_concurrency() -> int:
    """Chrome tabs per render. Measured on the M1 (120 frames): 2 -> 23 s, 3 -> 24 s, 4 -> 21 s, 6 -> 25 s, 8 -> 27 s.
    The M1 has 4 performance cores, so more tabs only add contention and memory. Drop to 2 when RAM is nearly gone."""
    if os.environ.get("RENDER_CONCURRENCY"):
        return max(1, int(os.environ["RENDER_CONCURRENCY"]))
    n = min(4, os.cpu_count() or 4)
    try:
        import subprocess
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        pg = int(vm.split("page size of ")[1].split()[0])
        avail = sum(int(l.split(":")[1].strip(" .\n")) for l in vm.splitlines()
                    if l.split(":")[0] in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")) * pg
        if avail < (1 << 30):  # under ~1 GB reclaimable: swapping, fewer tabs is faster
            n = min(n, 2)
    except Exception:
        pass
    return n

REVIEW_MIN = float(os.environ.get("REVIEW_MIN", "5.0"))   # AI review score below this => one automatic redo
REVIEW_REDOS = int(os.environ.get("REVIEW_REDOS", "1"))   # max automatic redos per clip (speed bound)
USD_IN = float(os.environ.get("GEMINI_USD_IN_PER_M", "0.25"))   # legacy fallback for unknown models
USD_OUT = float(os.environ.get("GEMINI_USD_OUT_PER_M", "1.50"))
# Model roles: a cheap model screens every candidate, a stronger one judges the shortlist, plans and reviews.
SCREEN_MODEL = os.environ.get("SCREEN_MODEL", "gemini-3.1-flash-lite")
MAIN_MODEL = os.environ.get("MAIN_MODEL", "gemini-3.8-flash")
JUDGE_TOP = int(os.environ.get("JUDGE_TOP", "3"))  # shortlist size re-judged by MAIN_MODEL
# USD per 1M tokens (in, out incl. thinking), standard paid tier, from Google's pricing page (checked 2026-11-14).
PRICES = {
    "gemini-3.1-flash-lite": [("2000-01-01", 0.25, 1.50)],
    "gemini-3.5-flash-lite": [("2000-01-01", 0.30, 2.50)],
    "gemini-3.8-flash": [("2000-01-01", 0.75, 3.75), ("2027-01-01", 1.50, 7.50)],
    "gemini-4-hydrogen": [("2000-01-01", 1.50, 7.50)],
    "gemini-3.5-flash": [("2000-01-01", 0.30, 2.50)],  # no longer listed; historical rows only
}
# LLM fallback when Gemini hits quota/5xx: same model via OpenRouter, then Groq (frames only). Keys live in .env.
FALLBACK_ENABLED = os.environ.get("FALLBACK_ENABLED", "1") == "1"
OPENROUTER_MODELS = {"gemini-3.1-flash-lite": "google/gemini-3.1-flash-lite", "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
                     "gemini-3.8-flash": "google/gemini-3.8-flash", "gemini-4-hydrogen": "google/gemini-4-hydrogen"}
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_USD_IN = float(os.environ.get("GROQ_USD_IN_PER_M", "0.50"))   # estimate; Groq does not return cost
GROQ_USD_OUT = float(os.environ.get("GROQ_USD_OUT_PER_M", "1.50"))
# Spending limits (USD, estimated at paid rates). Enforced before AI calls.
BUDGET_JOB = float(os.environ.get("BUDGET_JOB_USD", "0.10"))
BUDGET_DAY = float(os.environ.get("BUDGET_DAY_USD", "0.50"))
# Caption timing: energy-based word re-timing did not measurably help on real game audio (111->105 ms and
# 110->112 ms mean error on two clips), so it is off by default; a proper forced-alignment model is the real fix.
CAPTION_REFINE = os.environ.get("CAPTION_REFINE", "0") == "1"
REFRAME = os.environ.get("REFRAME", "1") == "1"  # auto-reframe: camera path that follows the action (see reframe.py)
CAPTION_ALIGN = os.environ.get("CAPTION_ALIGN", "1") == "1"  # wav2vec2 forced alignment (English); see align.py
CAPTION_LEAD_S = float(os.environ.get("CAPTION_LEAD_S", "0"))
BUDGET_TOTAL = float(os.environ.get("BUDGET_TOTAL_USD", "4.00"))
BUDGET_SINCE = os.environ.get("BUDGET_SINCE", "")  # ISO time paid usage started; the total limit counts from here


# Free tier: calls to these models on the free Gemini tier cost $0, so they are recorded as $0 (the estimate stays in the
# usage row) and do not eat the spending limits. OpenRouter/Groq fallbacks are paid and always counted.
FREE_TIER = os.environ.get("GEMINI_FREE_TIER", "0") == "1"
FREE_TIER_MODELS = {m.strip() for m in os.environ.get("GEMINI_FREE_TIER_MODELS", "gemini-3.1-flash-lite").split(",") if m.strip()}


def price(model: str, when=None) -> tuple[float, float]:
    import datetime as _dt
    day = (when or _dt.date.today()).isoformat()
    rows = PRICES.get(model) or PRICES.get(model.split("-preview")[0]) or [("2000-01-01", USD_IN, USD_OUT)]
    cur = rows[0]
    for r in rows:
        if r[0] <= day:
            cur = r
    return cur[1], cur[2]
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "8"))
MIN_QUALITY = float(os.environ.get("MIN_QUALITY", "6.0"))  # 0-10, from pass 2
MIN_CLIPS = int(os.environ.get("MIN_CLIPS", "3"))  # clip floor per job (see gemini.clip_floor)
FLOOR_QUALITY_DROP = float(os.environ.get("FLOOR_QUALITY_DROP", "2.0"))  # how far under MIN_QUALITY a moment may be to fill the floor
PUBLIC_BASE = f"http://127.0.0.1:{PORT}"


def job_dir(job_id: str) -> Path:
    d = JOBS / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d
