"""Channel watcher: new uploads / finished streams on the YouTube channels in Settings, found via YouTube's public
RSS feed (no key, no login, metadata only). It only REPORTS new videos (n8n sends a Telegram message with a link
that opens the New job form pre-filled). Nothing is downloaded or clipped until the owner submits the job."""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from . import config

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
UA = {"User-Agent": "gaming-clipper/0.1"}
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def _state_file():
    return config.DATA / "watch_state.json"


def _load() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {"seen": {}, "channel_ids": {}}


def resolve(entry: str, state: dict) -> str | None:
    """'UC...' id, a channel URL, or an @handle -> channel id (looked up once from the public channel page, then cached)."""
    e = entry.strip()
    m = re.search(r"(UC[A-Za-z0-9_-]{22})", e)
    if m:
        return m.group(1)
    if e in state["channel_ids"]:
        return state["channel_ids"][e]
    handle = e if e.startswith("http") else f"https://www.youtube.com/{e if e.startswith('@') else '@' + e}"
    try:  # yt-dlp reads the channel's metadata (the plain web page can be a cookie-consent wall in the EU)
        import subprocess
        out = subprocess.run(["yt-dlp", "--no-cookies", "--skip-download", "--flat-playlist", "--playlist-items", "1",
                              "--print", "%(playlist_channel_id)s %(channel_id)s", handle + "/videos"], capture_output=True, text=True, timeout=90).stdout
        m = re.search(r"(UC[A-Za-z0-9_-]{22})", out)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if m:
        state["channel_ids"][e] = m.group(1)
        return m.group(1)
    return None


def parse_feed(xml: str) -> list[dict]:
    root = ET.fromstring(xml)
    author = root.findtext("a:title", default="", namespaces=NS)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        out.append({"video_id": vid, "title": e.findtext("a:title", default="", namespaces=NS), "channel": author,
                    "published": e.findtext("a:published", default="", namespaces=NS),
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def check(channels: list[str], dashboard: str = "http://127.0.0.1:3001") -> dict:
    """New videos since the last check. The first check of a channel only records what is there (no flood of old videos)."""
    st = _load()
    new, errors = [], []
    for ch in channels:
        cid = resolve(ch, st)
        if not cid:
            errors.append(f"{ch}: channel not found")
            continue
        try:
            r = httpx.get(FEED.format(cid), headers=UA, timeout=20)
            r.raise_for_status()
            items = parse_feed(r.text)
        except (httpx.HTTPError, ET.ParseError) as e:
            errors.append(f"{ch}: {str(e)[:120]}")
            continue
        first = cid not in st["seen"]
        seen = set(st["seen"].get(cid, []))
        for it in items:
            if it["video_id"] not in seen and not first:
                it["clip_link"] = f"{dashboard}/new?url={quote(it['url'], safe='')}&title={quote(it['title'][:120], safe='')}"
                new.append(it)
        st["seen"][cid] = list(seen | {it["video_id"] for it in items})[-200:]
    _state_file().write_text(json.dumps(st))
    return {"checked": len(channels), "new": new, "errors": errors}
