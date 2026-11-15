"""Record live streams (Twitch, Kick, YouTube) while they are on, in gapless chunks that become normal jobs.

One recorder (ffmpeg's segment muxer on the stream's media URL) per live channel, so there is no gap between chunks. Each
finished chunk is remuxed into inbox/ and, when the stream has `auto_clip`, submitted as a normal job (the job's `live` meta
carries the platform, channel and the chunk's wall-clock start, which the analysis uses for Twitch viewer clips and ad-break
detection). Clips are never published automatically.

Safety: a stream can only be added with `rights_confirmed` (you own it or have the creator's permission), only the three
platform hosts are accepted, recorders are separate processes (a worker restart does not stop a recording; the pid file is
adopted again), and recording is refused when the disk is nearly full. Old chunks are deleted after LIVE_KEEP_DAYS.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import config

LIVE_DIR = config.DATA / "live"
STREAMS_FILE = config.DATA / "live_streams.json"
POLL_S = int(os.environ.get("LIVE_POLL_S", "90"))
CHUNK_S = int(os.environ.get("LIVE_CHUNK_S", "1800"))
MIN_CHUNK_S = float(os.environ.get("LIVE_MIN_CHUNK_S", "90"))
MIN_FREE_GB = float(os.environ.get("LIVE_MIN_FREE_GB", "10"))
KEEP_DAYS = float(os.environ.get("LIVE_KEEP_DAYS", "3"))
ENABLED = os.environ.get("LIVE_ENABLED", "1") == "1"
HOSTS = {"twitch": "https://www.twitch.tv/{}", "kick": "https://kick.com/{}", "youtube": "https://www.youtube.com/@{}/live"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HOST_RE = re.compile(r"^https://(www\.)?(twitch\.tv|kick\.com|youtube\.com)/@?([A-Za-z0-9_.-]+)(/live)?/?$")

_lock = threading.RLock()
_state: dict[str, dict] = {}          # name -> runtime status (not persisted)
_procs: dict[str, "Recorder"] = {}
_submit = None                         # injected by main: submit(stream, filename, started_at) -> job id
_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()


# --------------------------------------------------------------------------- pure helpers
def parse_ref(ref: str) -> tuple[str, str, str]:
    """'twitch:xqc', 'kick:xqc', 'youtube:@KaiCenat' or a platform URL -> (platform, channel, live url)."""
    ref = (ref or "").strip()
    if ref.startswith("http"):
        m = HOST_RE.match(ref)
        if not m:
            raise ValueError("only twitch.tv, kick.com and youtube.com channel links are accepted")
        platform = {"twitch.tv": "twitch", "kick.com": "kick", "youtube.com": "youtube"}[m.group(2)]
        channel = m.group(3)
    else:
        platform, _, channel = ref.partition(":")
        channel = channel.lstrip("@")
        if platform not in HOSTS or not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", channel):
            raise ValueError("use twitch:name, kick:name, youtube:@handle or a channel link")
    return platform, channel, HOSTS[platform].format(channel)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "stream"


def topic_ok(stream: dict, info: dict) -> bool:
    """Record only while the stream is about the topic (comma-separated keywords in `topic`); empty topic = always."""
    words = [w.strip().lower() for w in (stream.get("topic") or "").split(",") if w.strip()]
    if not words:
        return True
    hay = " ".join([info.get("title") or "", info.get("description") or "", " ".join(info.get("tags") or [])]).lower()
    return any(w in hay for w in words)


def chunk_start(path: Path) -> float | None:
    """Wall-clock start of a segment from its name seg-YYYYmmdd-HHMMSS.ts (ffmpeg strftime = local time)."""
    m = re.search(r"seg-(\d{8})-(\d{6})", path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return None


def ffmpeg_cmd(media_urls: list[str], out_dir: Path, chunk_s: int = CHUNK_S) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "10"]
    for u in media_urls[:2]:
        cmd += ["-i", u]
    if len(media_urls) >= 2:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(chunk_s), "-reset_timestamps", "1", "-strftime", "1",
            "-segment_format", "mpegts", str(out_dir / "seg-%Y%m%d-%H%M%S.ts")]
    return cmd


# --------------------------------------------------------------------------- persistence
def load_streams() -> list[dict]:
    with _lock:
        try:
            return json.loads(STREAMS_FILE.read_text())
        except (OSError, ValueError):
            return []


def _save(streams: list[dict]):
    STREAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STREAMS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(streams, indent=1))
    tmp.replace(STREAMS_FILE)


def add_stream(ref: str, rights_confirmed: bool, topic: str = "", game: str = "", auto_clip: bool = True,
               clips: int = 3, style: str | None = None, name: str | None = None, template: str = "gta6_gameplay",
               captions: str = "off", gameplay_crop: str = "auto", facecam_size: str = "medium",
               facecam_position: str = "top_left") -> dict:
    if not rights_confirmed:
        raise ValueError("confirm that you own this stream or have the creator's permission to clip it")
    platform, channel, url = parse_ref(ref)
    nm = name or slug(f"{channel}-{platform}")
    if not NAME_RE.match(nm):
        raise ValueError("name must be lowercase letters, digits and dashes")
    s = {"name": nm, "platform": platform, "channel": channel, "url": url, "topic": topic.strip()[:200],
         "game": game.strip()[:60], "auto_clip": bool(auto_clip), "clips": max(1, min(8, int(clips))), "style": style,
         "template": template, "captions": captions, "gameplay_crop": gameplay_crop, "facecam_size": facecam_size,
         "facecam_position": facecam_position,
         "enabled": True, "rights_confirmed": True, "added": time.time()}
    with _lock:
        streams = [x for x in load_streams() if x["name"] != nm]
        streams.append(s)
        _save(streams)
    _wake.set()
    return s


def update_stream(name: str, **fields) -> dict:
    allowed = {"enabled", "auto_clip", "topic", "game", "clips", "style", "template", "captions", "gameplay_crop",
               "facecam_size", "facecam_position"}
    with _lock:
        streams = load_streams()
        for s in streams:
            if s["name"] == name:
                for k, v in fields.items():
                    if k in allowed and v is not None:
                        s[k] = v
                _save(streams)
                _wake.set()
                return s
    raise KeyError(name)


def remove_stream(name: str) -> bool:
    with _lock:
        streams = load_streams()
        keep = [s for s in streams if s["name"] != name]
        if len(keep) == len(streams):
            return False
        _save(keep)
    stop_recorder(name)
    return True


# --------------------------------------------------------------------------- probing and recording
def probe(url: str) -> dict:
    """{'live': bool, 'title', 'description', 'tags'} via yt-dlp (no login). A probe failure counts as not live."""
    try:
        r = subprocess.run(["yt-dlp", "-J", "--no-warnings", "--skip-download", "--no-playlist", "--no-cookies", url],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return {"live": False, "error": "probe timed out"}
    if r.returncode != 0 or not r.stdout.strip():
        return {"live": False, "error": (r.stderr or "").strip().splitlines()[-1][:160] if r.stderr.strip() else "offline"}
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return {"live": False, "error": "unreadable probe output"}
    title = info.get("fulltitle") or info.get("title") or ""
    if re.fullmatch(r".+ \(live\)", title) and info.get("description"):
        title = info["description"]  # Twitch: yt-dlp's title is "<name> (live)"; the stream title is the description
    return {"live": bool(info.get("is_live") or info.get("live_status") == "is_live"), "title": title[:200],
            "description": (info.get("description") or "")[:500], "tags": info.get("tags") or [],
            "channel": info.get("channel") or info.get("uploader")}


def media_urls(url: str) -> list[str]:
    r = subprocess.run(["yt-dlp", "-g", "--no-warnings", "--no-playlist", "--no-cookies", "-f", "best[height<=1080]/best", url],
                       capture_output=True, text=True, timeout=90)
    urls = [u for u in r.stdout.split() if u.startswith("http")]
    if r.returncode != 0 or not urls:
        raise RuntimeError((r.stderr or "no media url").strip().splitlines()[-1][:200])
    return urls


class Recorder:
    def __init__(self, name: str, proc: subprocess.Popen | None = None, pid: int | None = None):
        self.name, self.proc, self.pid = name, proc, pid or (proc.pid if proc else 0)
        self.since = time.time()

    def alive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def stop(self):
        try:
            os.killpg(self.pid, signal.SIGINT)  # ffmpeg finishes the current segment cleanly on SIGINT
        except (OSError, ProcessLookupError):
            return
        for _ in range(20):
            if not self.alive():
                return
            time.sleep(0.5)
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def _dir(name: str) -> Path:
    d = LIVE_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_recorder(stream: dict) -> None:
    name = stream["name"]
    free = shutil.disk_usage(config.DATA).free / 1e9
    if free < MIN_FREE_GB:
        raise RuntimeError(f"only {free:.1f} GB free (need {MIN_FREE_GB:.0f} GB): not recording")
    urls = media_urls(stream["url"])
    d = _dir(name)
    log = (d / "ffmpeg.log").open("ab")
    proc = subprocess.Popen(ffmpeg_cmd(urls, d), stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    (d / "pid").write_text(str(proc.pid))
    _procs[name] = Recorder(name, proc)


def stop_recorder(name: str):
    rec = _procs.pop(name, None)
    if rec:
        rec.stop()


def _adopt():
    """After a worker restart a recorder may still be running: track its pid instead of starting a second one."""
    for s in load_streams():
        pf = LIVE_DIR / s["name"] / "pid"
        if s["name"] in _procs or not pf.exists():
            continue
        try:
            pid = int(pf.read_text())
            os.kill(pid, 0)
            r = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout
            if "ffmpeg" in r:
                _procs[s["name"]] = Recorder(s["name"], None, pid)
        except (OSError, ValueError):
            pf.unlink(missing_ok=True)


# --------------------------------------------------------------------------- chunks -> jobs
def finished_chunks(name: str, recording: bool) -> list[Path]:
    """Segments that will not grow any more: every one when no recorder runs, else all but the newest."""
    segs = sorted(_dir(name).glob("seg-*.ts"))
    if recording and segs:
        segs = segs[:-1]
    return [p for p in segs if time.time() - p.stat().st_mtime > 15]


def process_chunk(stream: dict, seg: Path) -> str | None:
    """Remux one finished segment into inbox/ and submit it. Returns the job id (or None if dropped / not auto_clip)."""
    started = chunk_start(seg) or seg.stat().st_mtime
    from . import media
    try:
        dur = media.probe(seg)["duration"]
    except Exception:  # noqa: BLE001 - unreadable segment
        dur = 0
    if dur < MIN_CHUNK_S:
        seg.unlink(missing_ok=True)
        return None
    fname = f"live-{stream['name']}-{datetime.fromtimestamp(started).strftime('%Y%m%d-%H%M%S')}.mp4"
    dst = config.INBOX / fname
    tmp = dst.with_suffix(".part.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(seg), "-c", "copy",
                        "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(tmp)], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:  # audio that is not AAC (no bitstream filter needed)
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(seg), "-c", "copy",
                            "-movflags", "+faststart", str(tmp)], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"remux failed: {r.stderr[-200:]}")
    tmp.replace(dst)
    seg.unlink(missing_ok=True)
    if not (stream.get("auto_clip") and _submit):
        return None
    return _submit(stream, fname, started)


# --------------------------------------------------------------------------- the loop
def _tick():
    streams = [s for s in load_streams() if s.get("enabled", True)]
    _adopt()
    probes: dict[str, dict] = {}
    todo = [s for s in streams if not (_procs.get(s["name"]) and _procs[s["name"]].alive())]
    if todo:
        with ThreadPoolExecutor(4) as pool:
            for s, info in zip(todo, pool.map(lambda x: probe(x["url"]), todo)):
                probes[s["name"]] = info
    for s in streams:
        name = s["name"]
        st = _state.setdefault(name, {})
        st["checked"] = time.time()
        rec = _procs.get(name)
        if rec and not rec.alive():
            _procs.pop(name, None)
            rec = None
            st["recording"] = False
        if rec:
            newest = max((p.stat().st_mtime for p in _dir(name).glob("seg-*.ts")), default=rec.since)
            if time.time() - newest > 180:  # alive but nothing written for 3 minutes: stalled, restart next tick
                rec.stop()
                _procs.pop(name, None)
                st.update(recording=False, error="recording stalled; restarting")
            else:
                st["recording"] = True
        elif name in probes:
            info = probes[name]
            st.update(live=info["live"], title=info.get("title"), error=info.get("error") if not info["live"] else None)
            if info["live"] and topic_ok(s, info):
                try:
                    start_recorder(s)
                    st.update(recording=True, error=None, since=time.time())
                except Exception as e:  # noqa: BLE001
                    st.update(recording=False, error=str(e)[:200])
            elif info["live"]:
                st["error"] = "live, but not on the topic: not recording"
        for seg in finished_chunks(name, bool(_procs.get(name))):
            try:
                job = process_chunk(s, seg)
                st["chunks"] = st.get("chunks", 0) + 1
                if job:
                    st["last_job"] = job
            except Exception as e:  # noqa: BLE001 - keep the segment so the next tick retries it
                st["error"] = f"chunk {seg.name}: {str(e)[:160]}"
                break
    _cleanup()


def _cleanup():
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in config.INBOX.glob("live-*.mp4"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
    for p in LIVE_DIR.glob("*/seg-*.ts"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


def _loop():
    while not _stop.is_set():
        try:
            _tick()
        except Exception as e:  # noqa: BLE001 - the loop must survive anything
            _state.setdefault("_loop", {})["error"] = str(e)[:200]
        _wake.wait(POLL_S)
        _wake.clear()


def start(submit) -> None:
    global _thread, _submit
    _submit = submit
    if not ENABLED or (_thread and _thread.is_alive()):
        return
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="live-recorder", daemon=True)
    _thread.start()


def check_now():
    _wake.set()


def status() -> list[dict]:
    out = []
    for s in load_streams():
        st = _state.get(s["name"], {})
        rec = _procs.get(s["name"])
        out.append({**{k: v for k, v in s.items() if k != "rights_confirmed"}, "recording": bool(rec and rec.alive()),
                    "live": st.get("live"), "title": st.get("title"), "error": st.get("error"), "chunks": st.get("chunks", 0),
                    "last_job": st.get("last_job"), "checked": st.get("checked"),
                    "recording_since": rec.since if rec and rec.alive() else None})
    return out
