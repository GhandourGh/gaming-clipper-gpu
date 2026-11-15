"""Spending limits for AI calls (estimated at paid Gemini rates). Hard caps: work that would cross a limit is not started."""
from . import config, db

# Conservative per-call estimates (USD), from measured token counts on real jobs.
EST = {"textscore": 0.006, "screen": 0.003, "judge": 0.012, "director": 0.02, "review": 0.01, "moderation": 0.005}
PER_CLIP = EST["director"] * 1.5 + EST["review"]  # one plan (+ possible repair) and one review


def spent(job_id: str | None = None) -> dict:
    r = db.q1("""select coalesce(sum(usd) filter (where job_id=%s and ts >= %s::timestamptz),0) as job,
                        coalesce(sum(usd) filter (where ts > greatest(date_trunc('day', now()), %s::timestamptz)),0) as day,
                        coalesce(sum(usd) filter (where ts >= %s::timestamptz),0) as total from costs""",
               (job_id, config.BUDGET_SINCE or "2000-01-01", config.BUDGET_SINCE or "2000-01-01", config.BUDGET_SINCE or "2000-01-01"))
    return {k: float(v) for k, v in r.items()}


def remaining(job_id: str | None = None) -> dict:
    s = spent(job_id)
    return {"job": config.BUDGET_JOB - s["job"], "day": config.BUDGET_DAY - s["day"], "total": config.BUDGET_TOTAL - s["total"]}


def blocked(job_id: str | None, est: float) -> str | None:
    """Reason text when spending `est` more would cross a limit, else None."""
    r = remaining(job_id)
    for k, name, lim in (("total", "total", config.BUDGET_TOTAL), ("day", "daily", config.BUDGET_DAY), ("job", "per-job", config.BUDGET_JOB)):
        if job_id is None and k == "job":
            continue
        if r[k] < est:
            return f"Spending limit reached: {name} limit ${lim:.2f} (left ${max(0.0, r[k]):.3f}). Raise it in Settings or wait{' until tomorrow' if k == 'day' else ''}."
    return None


def status() -> dict:
    s = spent(None)
    return {"since": config.BUDGET_SINCE, "limits": {"job": config.BUDGET_JOB, "day": config.BUDGET_DAY, "total": config.BUDGET_TOTAL},
            "spent": {"day": s["day"], "total": s["total"]},
            "left": {"day": config.BUDGET_DAY - s["day"], "total": config.BUDGET_TOTAL - s["total"]}}
