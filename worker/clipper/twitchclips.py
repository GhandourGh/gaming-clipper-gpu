"""Moments the stream's own viewers clipped on Twitch, as a pass-1 signal for a live recording.

Viewers clip a moment seconds after it happens, so the clip's `created_at` is the best timestamp there is: a chase, a crash
or a heist finale is often a scream and twelve seconds of nobody talking, which a transcript ranks low. For a recorded
chunk we know when it started (wall clock), so a clip made during it lands at `created_at - chunk_start`.

Needs TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET (an app access token reads public clips; no viewer account is involved).
Without keys, or on any API problem, the signal is simply absent. Pure helpers are unit-tested; the HTTP calls are not
exercised without keys.
"""
import math
import os
from datetime import datetime, timezone

import httpx
import numpy as np

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
MAX_PAGES = 5  # 100 clips per page, most viewed first


def configured() -> bool:
    return bool(os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET"))


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def clips_to_signal(clips: list[dict], chunk_start: float, n: int, spread: float = 6.0) -> list[float]:
    """Per-second 0-1 curve. Each clip adds a triangular bump over the `duration` seconds before its creation (plus a little
    after), weighted by log(views). A moment several people clipped counts more than one clip."""
    out = np.zeros(n)
    for c in clips:
        try:
            made = parse_time(c["created_at"]) - chunk_start
            dur = float(c.get("duration") or 30)
            w = math.log1p(max(0, int(c.get("view_count") or 0))) + 1.0
        except (KeyError, TypeError, ValueError):
            continue
        lo, hi = made - dur, made + 3.0
        a, b = max(0, int(lo)), min(n, int(hi) + 1)
        if b <= a:
            continue
        t = np.arange(a, b)
        tri = 1.0 - np.abs((t - (lo + hi) / 2) / ((hi - lo) / 2 + 1e-6))
        out[a:b] += w * np.clip(tri, 0, 1)
    out = np.convolve(out, np.ones(int(spread)) / spread, "same") if spread > 1 else out
    top = np.percentile(out[out > 0], 95) if (out > 0).any() else 0
    return np.clip(out / top, 0, 1).round(3).tolist() if top > 0 else [0.0] * n


def _token(client: httpx.Client) -> str:
    r = client.post(TOKEN_URL, params={"client_id": os.environ["TWITCH_CLIENT_ID"],
                                       "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
                                       "grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_clips(login: str, start: float, end: float) -> list[dict]:
    if not configured():
        return []
    with httpx.Client(timeout=30) as c:
        h = {"Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {_token(c)}"}
        u = c.get(f"{HELIX}/users", params={"login": login}, headers=h)
        u.raise_for_status()
        data = u.json().get("data") or []
        if not data:
            return []
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            q = {"broadcaster_id": data[0]["id"], "started_at": _iso(start), "ended_at": _iso(end), "first": 100}
            if cursor:
                q["after"] = cursor
            r = c.get(f"{HELIX}/clips", params=q, headers=h)
            r.raise_for_status()
            j = r.json()
            out += j.get("data") or []
            cursor = (j.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out


def clip_signal(login: str, chunk_start: float, n: int) -> list[float]:
    """Wait-free entry used by the analysis stage. Clips made right at the end of a chunk may not be listed yet, which only
    weakens the last seconds."""
    clips = fetch_clips(login, chunk_start, chunk_start + n + 120)
    return clips_to_signal(clips, chunk_start, n)
