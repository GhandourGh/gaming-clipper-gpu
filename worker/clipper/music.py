"""Background music from licensed sources, picked per clip by mood.
Order: Freesound (CC0 only, needs FREESOUND_API_KEY) -> Jamendo (CC BY / BY-SA, needs JAMENDO_CLIENT_ID)
-> Openverse audio (CC0 / BY, no key). NonCommercial and NoDerivatives licences are always rejected.
Every pick is logged with source, licence and author; tracks are cached by query."""
import hashlib
import html
import json
import os
from pathlib import Path

import httpx

from . import config, db

UA = {"User-Agent": "gaming-clipper/0.1 (personal project)"}
FREESOUND = "https://freesound.org/apiv2/search/"
JAMENDO = "https://api.jamendo.com/v3.0/tracks/"
OPENVERSE_AUDIO = "https://api.openverse.org/v1/audio/"
MOODS = {  # director mood -> search words that find usable background tracks
    "tense": "tense suspense", "epic": "epic cinematic", "upbeat": "upbeat energetic", "funny": "funny quirky",
    "chill": "chill lofi", "dark": "dark ambient", "action": "action drums", "sad": "sad piano", "hype": "hype electronic",
}


def keys() -> dict:
    return {"freesound": bool(os.environ.get("FREESOUND_API_KEY")), "jamendo": bool(os.environ.get("JAMENDO_CLIENT_ID")), "openverse": True}


def licence_ok(text: str | None) -> bool:
    """Allow CC0, public domain, CC BY and CC BY-SA. Reject NonCommercial / NoDerivatives / unknown."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in ("cc0", "pdm") or t.startswith("cc0") or t.startswith("cc 0") or "creative commons 0" in t or "publicdomain" in t or "/zero/" in t:
        return True
    if "/licenses/" in t:  # e.g. http://creativecommons.org/licenses/by-nc-nd/3.0/
        code = t.split("/licenses/", 1)[1].split("/", 1)[0]
    else:  # e.g. "by", "by-sa", "cc by-nc"
        code = t.removeprefix("cc").strip(" -_")
    parts = set(code.replace("_", "-").split("-"))
    return "by" in parts and not ({"nc", "nd"} & parts) and parts <= {"by", "sa"}


def _cache_path(key: str, ext: str) -> Path:
    config.ASSET_CACHE.mkdir(parents=True, exist_ok=True)
    return config.ASSET_CACHE / ("mus_" + hashlib.sha256(key.encode()).hexdigest()[:20] + ext)


def _download(url: str, key: str, params: dict | None = None) -> Path:
    dst = _cache_path(key, ".mp3")
    if not dst.exists():
        r = httpx.get(url, params=params, headers=UA, timeout=60, follow_redirects=True)
        r.raise_for_status()
        if len(r.content) < 20_000:
            raise ValueError("audio file too small")
        dst.write_bytes(r.content)
    return dst


def freesound(query: str, min_s: float) -> dict | None:
    key = os.environ.get("FREESOUND_API_KEY")
    if not key:
        return None
    r = httpx.get(FREESOUND, headers={**UA, "Authorization": f"Token {key}"}, timeout=20, params={
        "query": f"{query} music", "filter": f'license:"Creative Commons 0" duration:[{int(min_s)} TO 600]',
        "fields": "id,name,username,license,url,previews,duration,tags", "sort": "rating_desc", "page_size": 15})
    r.raise_for_status()
    for it in r.json().get("results", []):
        if "Creative Commons 0" not in (it.get("license") or "") and "publicdomain/zero" not in (it.get("license") or ""):
            continue
        prev = (it.get("previews") or {}).get("preview-hq-mp3")
        if not prev:
            continue
        f = _download(prev, f"fs:{it['id']}", {"token": key})
        return {"file": f, "provider": "freesound", "title": it.get("name"), "author": it.get("username"),
                "license": "CC0 1.0", "source": it.get("url"), "duration": it.get("duration")}
    return None


def jamendo(query: str, min_s: float) -> dict | None:
    cid = os.environ.get("JAMENDO_CLIENT_ID")
    if not cid:
        return None
    r = httpx.get(JAMENDO, headers=UA, timeout=20, params={
        "client_id": cid, "format": "json", "limit": 30, "fuzzytags": "+".join(query.split()),
        "durationbetween": f"{int(min_s)}_600", "audiodlformat": "mp32", "include": "licenses", "order": "popularity_total"})
    r.raise_for_status()
    for it in r.json().get("results", []):
        lic = it.get("license_ccurl") or ""
        if not it.get("audiodownload_allowed") or not it.get("audiodownload") or not licence_ok(lic):
            continue
        f = _download(it["audiodownload"], f"jm:{it['id']}")
        return {"file": f, "provider": "jamendo", "title": it.get("name"), "author": it.get("artist_name"),
                "license": lic, "source": it.get("shareurl"), "duration": it.get("duration")}
    return None


def openverse(query: str, min_s: float) -> dict | None:
    results = []
    for word in query.split():  # Openverse ANDs multi-word queries; one mood word at a time finds real tracks
        r = httpx.get(OPENVERSE_AUDIO, headers=UA, timeout=20, params={
            "q": word, "license": "cc0,by,by-sa,pdm", "category": "music", "mature": "false", "page_size": 20})
        r.raise_for_status()
        results += r.json().get("results", [])
        if results:
            break
    for it in results:
        dur = (it.get("duration") or 0) / 1000
        if not licence_ok(it.get("license")) or (dur and dur < min_s) or (it.get("filetype") or "mp3") not in ("mp3", "mp32"):
            continue
        try:
            f = _download(it["url"], f"ov:{it['id']}")
        except (httpx.HTTPError, ValueError):
            continue
        return {"file": f, "provider": f"openverse ({it.get('provider') or it.get('source')})", "title": html.unescape(it.get("title") or ""),
                "author": it.get("creator") or "unknown", "license": f"CC {str(it.get('license')).upper()} {it.get('license_version') or ''}".strip(),
                "source": it.get("foreign_landing_url") or it["url"], "duration": dur or None}
    return None


def pick(mood: str | None, min_s: float, job_id: str | None = None) -> dict | None:
    """First licensed track that fits; None when nothing usable is found (the clip renders without music)."""
    query = MOODS.get((mood or "").lower().strip(), mood or "background")
    meta = _cache_path(f"pick:{query}:{int(min_s // 15)}", ".json")
    if meta.exists():
        m = json.loads(meta.read_text())
        if Path(m["file"]).exists():
            return m
    errors = []
    for provider in (freesound, jamendo, openverse):
        try:
            m = provider(query, min_s)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            errors.append(f"{provider.__name__}: {str(e)[:120]}")
            continue
        if m:
            m["file"] = str(m["file"])
            meta.write_text(json.dumps(m))
            if job_id:
                db.add_cost(job_id, f"music:{m['provider'].split()[0]}", {"model": m["provider"].split()[0], "in": 0, "out": 0}, 0)
            return m
    if job_id and errors:
        db.event(job_id, "warn", "assets", "no music found: " + "; ".join(errors), {})
    return None
