"""Stage 5: resolve EDL overlays/music to licensed local files and log provenance.
Sources: Noto Emoji PNGs (Apache-2.0, Google), Openverse images filtered to
CC0 / CC-BY / PDM (commercial use allowed), bundled SFX synthesised by this
project. Everything is cached by query. No image generation by default."""
import hashlib
import json
import shutil

import httpx

from . import config, db
from .edl import EDL

UA = {"User-Agent": "gaming-clipper/0.1 (personal project)"}
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/svg/{}.svg"  # graphics CC-BY 4.0
OPENVERSE = "https://api.openverse.org/v1/images/"
SFX_DIR = config.RENDERER / "public" / "sfx"
OK_LICENSES = {"cc0", "pdm", "by"}


def _cache(key: str, ext: str):
    return config.ASSET_CACHE / (hashlib.sha256(key.encode()).hexdigest()[:20] + ext)


def emoji(ch: str) -> dict:
    cps = [f"{ord(c):x}" for c in ch if ord(c) != 0xFE0F]
    if not cps:
        raise ValueError("empty emoji")
    url = TWEMOJI.format("-".join(cps))
    dst = _cache(url, ".svg")
    if not dst.exists():
        r = httpx.get(url, headers=UA, timeout=20, follow_redirects=True)
        if r.status_code != 200 or b"<svg" not in r.content[:400]:
            raise ValueError(f"emoji {ch!r} not in Twemoji set ({r.status_code})")
        dst.write_bytes(r.content)
    return {"file": dst, "source": url, "license": "CC-BY 4.0", "author": "Twitter, Inc. and other contributors (Twemoji, jdecked fork)"}


def image(query: str) -> dict:
    meta_p = _cache("ov:" + query.lower(), ".json")
    if meta_p.exists():
        m = json.loads(meta_p.read_text())
        if (config.ASSET_CACHE / m["file"]).exists():
            m["file"] = config.ASSET_CACHE / m["file"]
            return m
    r = httpx.get(OPENVERSE, headers=UA, timeout=20, params={
        "q": query, "license": ",".join(sorted(OK_LICENSES)), "mature": "false", "page_size": 10,
        "extension": "png,jpg,jpeg"})
    r.raise_for_status()
    for it in r.json().get("results", []):
        if it.get("license") not in OK_LICENSES:
            continue
        try:
            img = httpx.get(it.get("thumbnail") or it["url"], headers=UA, timeout=20, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if img.status_code != 200 or not img.headers.get("content-type", "").startswith("image/"):
            continue
        ext = ".png" if "png" in img.headers["content-type"] else ".jpg"
        dst = _cache("ovimg:" + it["id"], ext)
        dst.write_bytes(img.content)
        m = {"file": dst.name, "source": it.get("foreign_landing_url") or it["url"], "license": f"CC {it['license'].upper()} {it.get('license_version') or ''}".strip(),
             "author": it.get("creator") or "unknown", "title": it.get("title"), "attribution": it.get("attribution")}
        meta_p.write_text(json.dumps(m))
        m["file"] = dst
        return m
    raise ValueError(f"no licensed image found for {query!r}")


def resolve(job_id, idx: int) -> dict:
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not clip or not clip["edl"]:
        raise ValueError("clip has no validated EDL")
    edl = EDL.model_validate(clip["edl"])
    pub = config.job_dir(job_id) / f"render_{idx}" / "public"
    (pub / "assets").mkdir(parents=True, exist_ok=True)
    manifest, overlays, dropped = [], [], []
    for i, o in enumerate(edl.overlays):
        if o.kind == "emoji":
            dropped.append({"overlay": i, "value": o.value, "error": "emoji overlays are disabled"})
            continue
        if o.kind == "text":
            overlays.append(o)
            continue
        try:
            a = emoji(o.value) if o.kind == "emoji" else image(o.value)
        except Exception as e:  # noqa: BLE001 - an unavailable asset must not kill the clip
            dropped.append({"overlay": i, "value": o.value, "error": str(e)[:200]})
            continue
        name = f"assets/ov{i}{a['file'].suffix}"
        shutil.copy2(a["file"], pub / name)
        overlays.append(o.model_copy(update={"asset_ref": name}))
        manifest.append({"use": f"overlay {i} ({o.kind}: {o.value})", "file": name, "source": a["source"],
                         "license": a["license"], "author": a["author"]})
    from .sfxlib import credit, file_for
    for s in edl.sfx:
        name = file_for(s.sound, s.at)
        (pub / name).parent.mkdir(parents=True, exist_ok=True)
        if not (pub / name).exists():
            shutil.copy2(SFX_DIR.parent / name, pub / name)
        if not any(m["file"] == name for m in manifest):
            manifest.append({"use": f"sfx {s.sound}", "file": name, **credit(name)})
    edl = edl.model_copy(update={"overlays": overlays})  # music is resolved by add_music() (its own n8n step)
    assets = {"manifest": manifest, "dropped": dropped}
    (pub.parent / "assets_manifest.json").write_text(json.dumps(manifest, indent=1))
    db.q("update clips set edl=%s, assets=%s, status='assets_ready' where job_id=%s and idx=%s",
         (db.J(edl.model_dump()), db.J(assets), job_id, idx))
    db.event(job_id, "info", "assets", f"clip {idx}: {len(manifest)} assets, {len(dropped)} dropped", assets)
    return assets


def add_music(job_id, idx: int) -> dict:
    """Pick a licensed track for the clip's mood (if the plan wants music and Settings allow it) and log it."""
    from . import music as mus
    from .manage import load_settings
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not clip or not clip["edl"]:
        raise ValueError("clip has no validated EDL")
    edl = EDL.model_validate(clip["edl"])
    pub = config.job_dir(job_id) / f"render_{idx}" / "public"
    mf = pub.parent / "assets_manifest.json"
    manifest = [m for m in (json.loads(mf.read_text()) if mf.exists() else []) if not m.get("use", "").startswith("music")]
    s = load_settings()
    m, reason = None, ""
    if not s.music_enabled:
        reason = "music is off in Settings"
    elif not edl.music.enabled:
        reason = "the edit plan has no music"
    else:
        dur = sum(x.out_len for x in edl.segments)
        m = mus.pick(edl.music.mood, min(dur, 60), job_id)
        reason = "" if m else "no licensed track found"
    if m:
        shutil.copy2(m["file"], pub / "music.mp3")
        music = edl.music.model_copy(update={"enabled": True, "asset_ref": "music.mp3", "gain_db": s.music_gain_db})
        manifest.append({"use": f"music ({edl.music.mood or 'background'})", "file": "music.mp3", "source": m["source"],
                         "license": m["license"], "author": m["author"], "title": m.get("title"), "provider": m["provider"]})
    else:
        music = edl.music.model_copy(update={"enabled": False, "asset_ref": None})
        (pub / "music.mp3").unlink(missing_ok=True)
    mf.write_text(json.dumps(manifest, indent=1))
    db.q("update clips set edl=%s where job_id=%s and idx=%s", (db.J(edl.model_copy(update={"music": music}).model_dump()), job_id, idx))
    db.event(job_id, "info", "assets", f"clip {idx}: music " + (f"{m['provider']}: {m.get('title')} ({m['license']})" if m else f"none ({reason})"), {})
    return {"music": bool(m), "reason": reason, "track": {k: m.get(k) for k in ("provider", "title", "author", "license", "source")} if m else None}
