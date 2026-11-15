"""Stage 1: bring the source video into the job folder.
Two authorised paths only:
  - file: a file the user placed in inbox/ (or uploaded via /upload)
  - youtube/twitch: yt-dlp with NO cookies / login / DRM workarounds, for videos the
    user owns or that are CC-licensed. Restricted videos fail cleanly."""
import hashlib
import json
import re
import shutil
import urllib.request
from pathlib import Path

from . import config, db, media
from .queue import Unrecoverable, stage

YT_RE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})(?:[&?#].*)?$")
TWITCH_RE = re.compile(r"^https?://(?:(?:www|m|go)\.)?twitch\.tv/(?:videos/|[^/?#]+/(?:v|video)/)(\d+)(?:[/?#].*)?$", re.I)
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def youtube_id(url: str) -> str | None:
    m = YT_RE.match(url.strip())
    return m.group(1) if m else None


def twitch_id(url: str) -> str | None:
    """A finished Twitch VOD id. Live channel pages are deliberately rejected: jobs need a stable completed source."""
    m = TWITCH_RE.match(url.strip())
    return m.group(1) if m else None


def inbox_file(name: str) -> Path:
    """Resolve a user-supplied filename strictly inside inbox/ (no traversal)."""
    p = (config.INBOX / Path(name).name).resolve()
    if p.parent != config.INBOX.resolve() or not p.is_file():
        raise Unrecoverable(f"file not found in inbox/: {Path(name).name}")
    if p.suffix.lower() not in VIDEO_EXT:
        raise Unrecoverable(f"unsupported file type: {p.suffix}")
    return p


def file_key(p: Path) -> str:
    """Fast content fingerprint: size + first/last 4 MB."""
    h = hashlib.sha256(str(p.stat().st_size).encode())
    with p.open("rb") as f:
        h.update(f.read(4 << 20))
        if p.stat().st_size > 8 << 20:
            f.seek(-(4 << 20), 2)
            h.update(f.read(4 << 20))
    return "file:" + h.hexdigest()[:32]


def finished_source(d: Path):
    """The completed source file, ignoring yt-dlp partials (source.f399.mp4.part, source.f251.webm)."""
    return next((f for f in sorted(d.glob("source.*")) if f.name.count(".") == 1 and f.suffix not in (".part", ".ytdl")), None)


def remote_duration(url: str) -> float | None:
    """Length of a YouTube video or Twitch VOD from its metadata (nothing downloaded)."""
    try:
        out = media.run(["yt-dlp", "--no-playlist", "--no-cookies", "--js-runtimes", "node", "--skip-download", "--print", "duration", url], timeout=120).stdout
        return float(out.strip().splitlines()[-1])
    except (RuntimeError, ValueError, IndexError):
        return None


def full_audio_score(job_id, url: str, d: Path):
    """Soundtrack of the whole stream (audio only, ~1/10 of the video size) -> per-second excitement score."""
    from .signals import audio_excitement
    wav = d / "full_audio.wav"
    try:
        if not wav.exists():
            media.run(["yt-dlp", "--no-playlist", "--no-cookies", "--js-runtimes", "node", "-f", "ba[ext=m4a]/ba",
                       "-o", str(d / "full_audio.%(ext)s"), url], timeout=3600)
            src = next(f for f in d.glob("full_audio.*") if f.suffix not in (".part", ".wav", ".ytdl"))
            media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                       "-c:a", "pcm_s16le", str(wav)], timeout=3600)
            src.unlink(missing_ok=True)
        return audio_excitement(wav)
    except (RuntimeError, StopIteration) as e:
        db.event(job_id, "warn", "ingest", f"full-audio scan failed: {str(e)[-200:]}", {})
        return None


def fetch_live_chat(job_id, url: str, d: Path):
    """Chat replay of a past YouTube live stream (text only, no video), used to find the moments chat exploded.
    Normal uploads have none; that is fine and is remembered so it is not retried."""
    try:
        media.run(["yt-dlp", "--no-playlist", "--no-cookies", "--js-runtimes", "node", "--skip-download", "--write-subs",
                   "--sub-langs", "live_chat", "-o", str(d / "source.%(ext)s"), url], timeout=1800)
    except RuntimeError as e:
        db.event(job_id, "warn", "ingest", f"live chat replay not available: {str(e)[-150:]}", {})
    if (d / "source.live_chat.json").exists():
        db.event(job_id, "info", "ingest", "live chat replay downloaded", {"bytes": (d / "source.live_chat.json").stat().st_size})
    else:
        (d / "chat.none").write_text("no live chat replay")


def fetch_twitch_chat(job_id, vod_id: str, d: Path):
    """Download the public replay chat for a finished Twitch VOD into a small provider-neutral JSONL timeline."""
    out = d / "source.twitch_chat.jsonl"
    if out.exists():
        return
    endpoint = "https://gql.twitch.tv/gql"
    query_hash = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
    cursor, rows, pages = None, [], 0
    try:
        while pages < 5000:
            variables = {"videoID": vod_id, "cursor": cursor} if cursor else {"videoID": vod_id, "contentOffsetSeconds": 0}
            body = {"operationName": "VideoCommentsByOffsetOrCursor",
                    "variables": variables,
                    "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}}}
            req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), method="POST",
                                         headers={"Client-ID": "ue6666qo983tsx6so1t0vnawi233wa",
                                                  "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed Twitch HTTPS endpoint
                payload = json.load(r)
            comments = (((payload.get("data") or {}).get("video") or {}).get("comments"))
            edges = (comments or {}).get("edges") or []
            if not edges:
                break
            for edge in edges:
                node = edge.get("node") or {}
                t = float(node.get("contentOffsetSeconds") or 0)
                fragments = ((node.get("message") or {}).get("fragments") or [])
                text = "".join(str(x.get("text") or "") for x in fragments).strip()
                if text:
                    rows.append({"t": round(t, 3), "text": text})
            pages += 1
            next_cursor = edges[-1].get("cursor")
            if not (comments.get("pageInfo") or {}).get("hasNextPage") or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        if rows:
            out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows))
            db.event(job_id, "info", "ingest", "Twitch chat replay downloaded", {"messages": len(rows), "pages": pages})
        else:
            (d / "chat.none").write_text("no Twitch chat replay")
    except Exception as e:  # noqa: BLE001 - chat improves ranking but must never block the VOD
        (d / "chat.none").write_text("Twitch chat replay unavailable")
        db.event(job_id, "warn", "ingest", f"Twitch chat replay not available: {str(e)[-180:]}", {})


def replay_chat(job_id, source_type: str, url: str, d: Path):
    if source_type == "twitch":
        fetch_twitch_chat(job_id, twitch_id(url) or "", d)
    else:
        fetch_live_chat(job_id, url, d)


def replay_chat_file(d: Path) -> Path:
    tw = d / "source.twitch_chat.jsonl"
    return tw if tw.exists() else d / "source.live_chat.json"


@stage("ingest")
def ingest(job_id, _clip):
    job = db.q1("select * from jobs where id=%s", (job_id,))
    d = config.job_dir(job_id)
    existing = finished_source(d)
    if existing is None:
        for f in d.glob("source.*"):  # leftovers of an interrupted download
            f.unlink()
        if job["source_type"] == "file":
            src = Path(job["source_path"])
            if not src.is_file():
                raise Unrecoverable("source file disappeared from inbox/")
            dst = d / f"source{src.suffix.lower()}"
            try:
                dst.hardlink_to(src)  # no extra disk
            except OSError:
                shutil.copy2(src, dst)
        else:
            tmpl = str(d / "source.%(ext)s")
            section_args = []
            total = remote_duration(job["source_url"])
            meta0 = dict(job["meta"] or {})
            if total and total > config.STREAM_SECTION_S and not meta0.get("full_video") and meta0.get("engine") != "stories":
                # long stream: chat replay first (text only), then download only the most exciting window
                if not (d / "source.live_chat.json").exists() and not (d / "chat.none").exists():
                    replay_chat(job_id, job["source_type"], job["source_url"], d)
                from .signals import audio_excitement, best_section
                score = None
                chat_path = replay_chat_file(d)
                if not chat_path.exists():  # no chat: judge the whole stream by its audio
                    score = full_audio_score(job_id, job["source_url"], d)
                # fewer clips wanted -> a shorter section (downloads are throttled to ~0.7 MB/s): 1 clip = 15 min
                want = meta0.get("clips")
                length = min(config.STREAM_SECTION_S, max(900.0, want * 700.0)) if want else config.STREAM_SECTION_S
                a, b, why = best_section(chat_path, total, length, score)
                section_args = ["--download-sections", f"*{a:.0f}-{b:.0f}"]
                meta0["section"] = [round(a), round(b)]
                meta0["source_total_s"] = total
                db.set_job(job_id, meta=meta0)
                db.event(job_id, "info", "ingest", f"long video ({total / 60:.0f} min): downloading {a / 60:.0f}-{b / 60:.0f} min ({why})", {"section": [a, b]})
            try:
                max_h = "720" if meta0.get("engine") == "stories" else "1080"
                media.run(["yt-dlp", "--no-playlist", "--no-cookies", "--js-runtimes", "node", "-f", f"bv*[height<={max_h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={max_h}]+ba/b[height<={max_h}]/b",
                           *section_args, "--merge-output-format", "mp4", "--write-info-json", "--no-write-comments", "-o", tmpl, job["source_url"]], timeout=7200)
            except RuntimeError as e:
                provider = "Twitch" if job["source_type"] == "twitch" else "YouTube"
                raise Unrecoverable(
                    f"{provider} retrieval failed without login/cookies (private, subscriber-only, age-restricted, expired, or blocked). "
                    f"Download the file from your own {provider} account and submit it as a file instead. "
                    f"Detail: {str(e)[-300:]}") from e
        existing = finished_source(d)
        if existing is None:
            raise RuntimeError("download finished but no source file was produced")
    if job["source_type"] != "file" and not replay_chat_file(d).exists() and not (d / "chat.none").exists():
        replay_chat(job_id, job["source_type"], job["source_url"], d)
    job = db.q1("select * from jobs where id=%s", (job_id,))  # meta may have gained the section above
    info = media.probe(existing)
    if not info["has_video"] or info["duration"] < 5:
        raise Unrecoverable("source has no video stream or is shorter than 5 s")
    if not info["has_audio"]:
        raise Unrecoverable("source has no audio track; highlight detection needs audio")
    meta = dict(job["meta"] or {})
    meta["source"] = info
    from .manage import load_settings
    if load_settings().detect_burned_captions and "burned_captions" not in meta:
        try:  # job flag: does the source already have subtitles burned into the picture?
            from .burned import detect
            meta["burned_captions"] = detect(existing, info["duration"])
        except Exception as e:  # noqa: BLE001 - detection must never block a job
            meta["burned_captions"] = {"present": False, "error": str(e)[:200]}
        bc = meta["burned_captions"]
        db.event(job_id, "info", "ingest", f"burned-in captions: {'yes (' + str(bc.get('where')) + ')' if bc.get('present') else 'no'}", bc)
    db.set_job(job_id, status="ingested", source_path=str(existing), meta=meta)
    from .queue import enqueue
    enqueue(job_id, "analyze")
    return {"source": info}
