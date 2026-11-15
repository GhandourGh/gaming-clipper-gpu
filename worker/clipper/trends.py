"""Trend hints for hooks and titles: what is trending around the video's game right now.
Providers (key in .env; the step is skipped when neither is set):
- ShortsIntel  GET /v1/keywords/{platform}?phrase=<game>  -> hashtags + phrases from top-performing Shorts (3 credits/lookup)
- TrendsMCP    POST /api mode=get_top_trends              -> live trend board, filtered to rows that mention the game
Results are cached for a day per game, so a job costs at most one lookup."""
import json
import os
import re
import time
from collections import Counter

import httpx

from . import config, db

SHORTSINTEL = "https://www.shortsintel.com/v1/keywords/{platform}"
TRENDSMCP = "https://api.trendsmcp.ai/api"
UA = {"User-Agent": "gaming-clipper/0.1"}
GAMES = [  # title pattern -> canonical game name (first match wins)
    (r"\bgta\s*(6|vi)\b|grand theft auto\s*(6|vi)\b", "GTA 6"), (r"\bgta\s*(5|v)\b|grand theft auto\s*(5|v)\b", "GTA 5"),
    (r"\bgta\b|grand theft auto", "GTA"), (r"fortnite", "Fortnite"), (r"minecraft", "Minecraft"), (r"valorant", "Valorant"),
    (r"warzone", "Warzone"), (r"call of duty|\bcod\b", "Call of Duty"), (r"apex", "Apex Legends"), (r"league of legends|\blol\b", "League of Legends"),
    (r"counter.?strike|\bcs2\b", "Counter-Strike 2"), (r"elden ring", "Elden Ring"), (r"dark souls", "Dark Souls"), (r"roblox", "Roblox"),
    (r"rocket league", "Rocket League"), (r"red dead", "Red Dead Redemption"), (r"\bfifa\b|\bea fc\b", "EA FC"),
]


def keys() -> dict:
    return {"shortsintel": bool(os.environ.get("SHORTSINTEL_API_KEY")), "trendsmcp": bool(os.environ.get("TRENDSMCP_API_KEY"))}


def game_of(*texts: str | None) -> str | None:
    t = " ".join(x for x in texts if x).lower()
    return next((name for pat, name in GAMES if re.search(pat, t)), None)


def _cache(provider: str, game: str):
    d = config.DATA / "cache" / "trends"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{provider}_{re.sub(r'[^a-z0-9]+', '-', game.lower())}_{time.strftime('%Y-%m-%d')}.json"


FILLER_TAGS = {"#fyp", "#foryou", "#foryoupage", "#viral", "#blowthisup", "#trending", "#xyzbca", "#fy", "#explore",
               "#shorts", "#short", "#reels", "#capcut", "#edit"}


def _caption_hook(desc: str) -> str:
    """The caption's words without hashtags/links: what the creator wrote as the hook."""
    t = re.sub(r"https?://\S+|#\w+", "", desc or "")
    return re.sub(r"\s+", " ", t).strip(" .-|")[:90]


def shortsintel(game: str) -> dict:
    """Top short videos for the game (TikTok by default: YouTube results carry no hashtags), weighted by views."""
    key = os.environ["SHORTSINTEL_API_KEY"]
    platform = os.environ.get("SHORTSINTEL_PLATFORM", "tiktok")
    r = httpx.get(SHORTSINTEL.format(platform=platform), headers={**UA, "Authorization": f"Bearer {key}"},
                  params={"phrase": game[:100], "wait": 60, "max_credits": 3}, timeout=90)
    if r.status_code == 202:
        raise RuntimeError("ShortsIntel still running (try again later)")
    r.raise_for_status()
    body = r.json()
    res = body.get("result") or {}
    tags, examples = Counter(), []
    for v in res.get("top_videos") or []:
        views = ((v.get("engagement") or {}).get("views") or 0) or 1
        seen = set()
        for h in list(v.get("hashtags") or []) + re.findall(r"#(\w+)", v.get("description") or ""):
            t = "#" + str(h).lstrip("#").lower()
            if t not in FILLER_TAGS and t not in seen:
                seen.add(t)
                tags[t] += views
        hook = _caption_hook(v.get("description"))
        if len(hook) >= 12 and len(examples) < 5:
            examples.append({"text": hook, "views": views})
    return {"provider": "shortsintel", "platform": platform, "game": game, "hashtags": [t for t, _ in tags.most_common(10)],
            "examples": examples, "phrases": [], "trend": (res.get("trend") or {}).get("direction"),
            "sampled": (res.get("stats") or {}).get("sampled_videos"), "credits": (body.get("run") or {}).get("charge_credits", 3)}


def trendsmcp(game: str) -> dict:
    key = os.environ["TRENDSMCP_API_KEY"]
    r = httpx.post(TRENDSMCP, headers={**UA, "Authorization": f"Bearer {key}"}, timeout=60,
                   json={"mode": "get_top_trends", "type": os.environ.get("TRENDSMCP_BOARD", "Google Trends"), "limit": 100})
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict) and d.get("statusCode", 200) >= 400:  # errors come back as HTTP 200 + envelope
        raise RuntimeError(f"TrendsMCP {d.get('statusCode')}: {str(d.get('body'))[:200]}")
    if isinstance(d, dict) and isinstance(d.get("body"), str):
        d = json.loads(d["body"])
    tokens = [t for t in re.findall(r"[a-z0-9]+", game.lower()) if len(t) >= 2]
    rows = [row[1] for row in d.get("data") or [] if isinstance(row, list) and len(row) > 1]
    hits = [k for k in rows if any(t in str(k).lower() for t in tokens)]
    return {"provider": "trendsmcp", "game": game, "hashtags": [], "phrases": hits[:10], "board_top": rows[:5],
            "requests": d.get("requests_charged")}


def hints(job_id: str, game: str | None, provider: str = "auto") -> dict:
    """Trend hints for the director. Never raises: returns {"skipped": reason} when not possible."""
    if not game:
        return {"skipped": "no game recognised in the title"}
    order = [p for p in ("shortsintel", "trendsmcp") if provider in ("auto", p)]
    errors = []
    for name in order:
        if not keys()[name]:
            errors.append(f"{name}: no key")
            continue
        c = _cache(name, game)
        if c.exists():
            return {**json.loads(c.read_text()), "cached": True}
        try:
            h = {"shortsintel": shortsintel, "trendsmcp": trendsmcp}[name](game)
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as e:
            errors.append(f"{name}: {str(e)[:150]}")
            continue
        c.write_text(json.dumps(h))
        usd = (h.get("credits") or 0) / 1000 if name == "shortsintel" else 0.0  # ShortsIntel: 1,000 credits per USD
        db.add_cost(job_id, f"trends:{name}", {"model": name, "in": 0, "out": 0, "credits": h.get("credits"), "requests": h.get("requests")}, usd)
        return {**h, "cached": False}
    return {"skipped": "; ".join(errors) or "no provider"}


def prompt_section(h: dict | None) -> str:
    if not h or h.get("skipped") or not (h.get("hashtags") or h.get("phrases") or h.get("examples")):
        return ""
    parts = []
    if h.get("hashtags"):
        parts.append("hashtags (most-viewed first): " + ", ".join(h["hashtags"][:8]))
    if h.get("phrases"):
        parts.append("words people use: " + ", ".join(h["phrases"][:8]))
    if h.get("examples"):
        parts.append("captions of top videos: " + " | ".join(f"\"{e['text']}\" ({e['views']:,} views)" for e in h["examples"][:4]))
    return (f"## Trending now around {h['game']} (optional hints, from {h['provider']})\n" + "\n".join(parts) +
            "\nUse one only if it truly fits this moment (hook, title or description). Never force a trend, never mislead, "
            "never copy a caption word for word.\n\n")
