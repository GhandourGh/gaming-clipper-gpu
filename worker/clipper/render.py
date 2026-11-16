"""Stage 6: EDL -> 1080x1920 MP4.
1) ffmpeg pre-cut: keep-segments from the source -> base.mp4 (short, CFR 30fps)
2) Remotion: layout, zooms, captions, overlays, SFX, hook -> raw.mp4
3) ffmpeg finish: loudness -14 LUFS / -1.5 dBTP, H.264 yuv420p, faststart -> final
Each step's output is kept, so a retry resumes at the failed step."""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, db, media
from .edl import EDL, to_output_time
from .queue import Unrecoverable, stage

FPS = 30
RENDERABLE = ("assets_ready", "rendering", "rendered", "qc_failed", "ready_for_review", "failed")


def detect_bars(src: Path, edl: EDL) -> str | None:
    """Black bars baked into the source picture (trailers are often 2.39:1 inside a 16:9 file) would show up as empty space
    above/below in a full-screen crop. Sample a few moments, keep the most conservative crop, and only use it if it matters."""
    if edl.layout.mode in ("split", "bubble"):  # facecam_box is relative to the full frame
        return None
    info = media.probe(src)
    W0, H0 = info["width"], info["height"]
    mids = sorted({(s.start + s.end) / 2 for s in edl.segments})
    times = [mids[len(mids) * k // 3] for k in range(3)] if len(mids) >= 3 else mids
    box = None
    for t in times or [1.0]:
        p = media.run(["ffmpeg", "-hide_banner", "-ss", f"{max(0, t - 0.75):.2f}", "-t", "1.5", "-i", str(src), "-vf",
                       "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"], timeout=120)
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", p.stderr)
        if not m:
            continue
        w, h, x, y = map(int, m[-1])
        b = (x, y, x + w, y + h)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if not box:
        return None
    w, h = (box[2] - box[0]) // 2 * 2, (box[3] - box[1]) // 2 * 2
    if w >= W0 * 0.97 and h >= H0 * 0.97:
        return None
    return f"crop={w}:{h}:{box[0] // 2 * 2}:{box[1] // 2 * 2}"


def _base(src: Path, edl: EDL, out: Path, src_info: dict):
    if out.exists():
        return
    parts, n = [], len(edl.segments)
    # Each story beat is its own seekable input. A single input spanning minute 15 to minute 140 would make ffmpeg
    # decode the two hours between them even though none of those frames are used.
    inputs = []
    for i, s in enumerate(edl.segments):
        dur = round(s.end - s.start, 3)
        inputs += [*media.hwdec(), "-ss", f"{s.start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
        slow = s.speed < 0.999  # slow motion: stretch the picture and the sound (pitch kept) so captions/effects stay in sync
        parts.append(f"[{i}:v]setpts=(PTS-STARTPTS)/{s.speed:.4f}[v{i}];" if slow else f"[{i}:v]setpts=PTS-STARTPTS[v{i}];")
        parts.append(f"[{i}:a]asetpts=PTS-STARTPTS,{f'atempo={s.speed:.4f},' if slow else ''}"
                     f"afade=t=in:d=0.02,afade=t=out:st={max(0, s.out_len - 0.03):.3f}:d=0.03[a{i}];")
    concat = "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca];"
    bars = detect_bars(src, edl)
    scale = f"[cv]{bars + ',' if bars else ''}fps=30,scale='min(1920,iw)':-2:flags=lanczos,format=yuv420p[vo]"
    tmp = out.with_suffix(".tmp.mp4")
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex",
               "".join(parts) + concat + scale, "-map", "[vo]", "-map", "[ca]", *media.h264("master"), "-g", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
               str(tmp)], timeout=1800)
    tmp.rename(out)


def _norm(t: str) -> str:
    return re.sub(r"[^\w']", "", t.lower())


def apply_caption_fixes(words: list[dict], fixes) -> list[dict]:
    """Replace misheard word sequences (case/punctuation-insensitive); merged words keep the first start and last end."""
    out = list(words)
    for f in fixes:
        wrong = [_norm(x) for x in f.wrong.split() if _norm(x)]
        if not wrong:
            continue
        i = 0
        while i + len(wrong) <= len(out):
            if [_norm(w["w"]) for w in out[i:i + len(wrong)]] == wrong:
                tail = re.sub(r"^.*?([^\w']*)$", r"\1", out[i + len(wrong) - 1]["w"])  # keep trailing punctuation
                s0, e0 = out[i]["s"], out[i + len(wrong) - 1]["e"]
                right = f.right.split()
                total = sum(len(x) for x in right) or 1
                new, t = [], s0
                for k, x in enumerate(right):  # one caption token per word, time shared by length
                    dt = (e0 - s0) * len(x) / total
                    new.append({"w": x + (tail if k == len(right) - 1 else ""), "s": round(t, 3), "e": round(t + dt, 3)})
                    t += dt
                out[i:i + len(wrong)] = new
                i += len(new)
                continue
            i += 1
    return out


def run_remotion(args: list[str], env: dict, timeout: int):
    """Run remotion in its own process group so a timeout or crash also kills the headless Chrome children
    (plain subprocess.run only kills npx and leaves Chrome tabs eating RAM)."""
    import signal
    npx = shutil.which("npx") or "/workspace/clipper-runtime/node-v20/bin/npx"
    pr = subprocess.Popen([npx, "remotion", *args], cwd=config.RENDERER, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env, start_new_session=True)
    try:
        out, err = pr.communicate(timeout=timeout)
    except BaseException:
        os.killpg(pr.pid, signal.SIGKILL)
        pr.wait()
        raise
    return subprocess.CompletedProcess(pr.args, pr.returncode, out, err)


def finalize(raw: Path, out: Path):
    """Loudness-normalise the audio and copy the video stream untouched (Remotion already encoded it at crf 17;
    re-encoding it again cost ~25 s per clip and lost quality)."""
    # the container's duration can claim a frame or two more than the file holds: trim the audio to the real frame count
    pk = media.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets",
                    "-of", "csv=p=0", str(raw)], timeout=120).stdout.strip()
    vdur = int(pk) / FPS if pk.isdigit() and int(pk) > 0 else media.probe(raw)["v_duration"]
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-map", "0:v:0", "-map", "0:a:0",
               "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{vdur:.3f}", "-t", f"{vdur:.3f}",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], timeout=600)


def _blur_bg(base: Path, out: Path):
    """Blurred, darkened 9:16 background for the 'fit' layout. Stored tiny (180x320) and stretched to full size by the
    renderer: it is a blur anyway, and decoding a full-size second video on every frame was the biggest optional cost."""
    if out.exists() and out.stat().st_mtime >= base.stat().st_mtime:
        return
    tmp = out.with_suffix(".tmp.mp4")
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base), "-an", "-vf",
               "scale=-2:320,crop=ih*9/16:ih,boxblur=12:2,eq=brightness=-0.12:saturation=1.3:contrast=0.9",
               *media.h264("bg"), "-r", "30", str(tmp)], timeout=600)
    tmp.rename(out)


def _music_props(edl: EDL) -> dict | None:
    if not (edl.music.enabled and edl.music.asset_ref):
        return None
    from .manage import load_settings
    return {"file": edl.music.asset_ref, "gainDb": edl.music.gain_db, "duckDb": load_settings().music_duck_db}


def captions_mode(job_id, idx: int | None = None) -> str:
    """overlay | bar | off: the clip's or the job's choice, else the Settings default."""
    from .manage import load_settings
    m = ((db.q1("select meta from jobs where id=%s", (job_id,)) or {}).get("meta") or {}).get("captions")
    if idx is not None:
        row = db.q1("select candidate from clips where job_id=%s and idx=%s", (job_id, idx)) or {}
        m = ((row.get("candidate") or {}).get("opts") or {}).get("captions") or m
    return m or load_settings().captions


def build_props(job_id, idx, edl: EDL, base_info: dict, tr: dict | None = None) -> dict:
    job_meta = (db.q1("select meta from jobs where id=%s", (job_id,)) or {}).get("meta") or {}
    gta = job_meta.get("template") == "gta6_gameplay"
    cap_mode = captions_mode(job_id, idx)
    if cap_mode == "off":
        edl = edl.model_copy(deep=True)
        edl.captions.enabled = False
    if tr is None:
        tr = json.loads((config.job_dir(job_id) / "analysis" / "transcript.json").read_text())
    segs = edl.segments
    ot = lambda t: to_output_time(segs, t)  # noqa: E731
    from .timing import refine_words  # Whisper word times are ~0.1 s off; re-time them from the audio
    ana = config.job_dir(job_id) / "analysis"
    lo, hi = min(s.start for s in segs) - 1, max(s.end for s in segs) + 1
    if not edl.captions.enabled:  # no captions: skip the word alignment (the slowest caption step)
        src_words = tr["words"]
    elif config.CAPTION_ALIGN and hi - lo <= 300:  # cross-timestamp stories are already refined per beat
        from .align import align_words
        src_words = align_words(tr["words"], ana / "audio.wav", lo, hi, tr.get("language"), cache=ana / "aligned.json")
    elif config.CAPTION_REFINE:
        src_words = refine_words(tr["words"], ana / "audio.wav", lo, hi)
    else:
        src_words = tr["words"]
    words = []
    for w in src_words:
        s, e = ot(w["s"]), ot(w["e"])
        if s is None:
            continue
        if e is None or e < s:  # word end falls in a cut: end it where its segment ends
            seg_out_end = next((to_output_time(segs, g.end) for g in segs if g.start <= w["s"] <= g.end), None)
            e = min(s + max(0.15, w["e"] - w["s"]), seg_out_end if seg_out_end is not None else s + 0.5)
        words.append({"w": w["w"], "s": round(s, 3), "e": round(min(e, base_info["duration"]), 3)})
    acc = 0.0  # captions inside a cold-open teaser: the same words also appear at the teaser's output time
    for g in segs:
        if g.role == "cold_open":
            for w in src_words:
                if w["s"] >= g.start and w["e"] <= g.end + 0.2:
                    words.append({"w": w["w"], "s": round(acc + (w["s"] - g.start) / g.speed, 3),
                                  "e": round(min(acc + g.out_len, acc + (w["e"] - g.start) / g.speed), 3)})
        acc += g.out_len
    words.sort(key=lambda w: w["s"])
    words = apply_caption_fixes(words, edl.caption_fixes)
    if config.CAPTION_LEAD_S:  # optional: show words slightly early
        for w in words:
            w["s"] = round(max(0.0, w["s"] - config.CAPTION_LEAD_S), 3)
    dur = base_info["duration"]
    # keep every caption word inside the rendered clip (the rendered video can be a few ms shorter than the plan)
    words = [{**w, "s": max(0.0, w["s"]), "e": round(min(w["e"], dur), 3)} for w in words if w["s"] < dur - 0.05]
    words = [w for w in words if w["e"] >= w["s"]]

    def span(st, en):
        s = ot(st)
        e = ot(en) if ot(en) is not None else s + (en - st)
        return round(s, 3), round(min(dur, max(s + 0.3, e)), 3)

    zooms = []
    for z in edl.zooms:
        s, e = span(z.start, z.end)
        tgt = "gameplay" if (edl.layout.mode in ("split", "bubble") and z.target == "frame") else z.target
        if z.target == "facecam" and edl.layout.mode not in ("split", "bubble"):
            tgt = "frame"
        zooms.append({"start": s, "end": e, "target": tgt, "scale": z.scale, "fx": z.focus_x, "fy": z.focus_y, "style": z.style})
    overlays = []
    for o in edl.overlays:
        s, e = span(o.start, o.end)
        overlays.append({"kind": o.kind, "start": s, "end": max(e, s + 0.6), "value": o.value, "file": o.asset_ref,
                         "position": o.position, "animation": o.animation})
    from .sfxlib import file_for
    from .sfxtime import align
    wav = config.job_dir(job_id) / "analysis" / "audio.wav"
    sfx = []
    for x in edl.sfx:  # each sound's loudest instant lands on the real action, to the 10 ms
        f = file_for(x.sound, x.at)
        lib = config.RENDERER / "public" / f
        st, _ = align(wav, x.at, lib, [o.start for o in edl.overlays] + [t.at for t in edl.transitions])
        o = ot(st)
        if o is None:  # the aligned start falls in a cut: start where the clip resumes, or keep the AI's time
            o = ot(x.at)
        sfx.append({"at": round(max(0.0, o), 3), "file": f, "gainDb": x.gain_db})
    trans = [{"at": round(ot(x.at), 3), "kind": x.kind} for x in edl.transitions]
    if segs[0].role == "cold_open" and not gta:  # mark the jump back in time after the teaser
        back = round(segs[0].end - segs[0].start, 3)
        trans.append({"at": back, "kind": "flash"})
        sfx.append({"at": max(0.0, back - 0.12), "file": "sfx/whoosh.wav", "gainDb": -6})
    lay = edl.layout
    logo_slug = job_meta.get("game_logo") or "gta6"
    logo_on = gta and logo_slug != "none" and (config.RENDERER / "public" / "logos" / f"{logo_slug}.png").exists()
    return {"style": edl.style, "fps": FPS, "duration": round(dur, 3), "src": "base.mp4", "srcW": base_info["width"], "srcH": base_info["height"],
            "template": job_meta.get("template", "auto"),
            "layout": {"mode": lay.mode, "facecamBox": lay.facecam_box.model_dump() if lay.facecam_box else None,
                       "gameplayFocusX": lay.gameplay_focus_x, "facecamShare": 0.5 if lay.mode == "split" else lay.facecam_share,
                       "bubbleCorner": lay.bubble_corner, "bubbleShape": lay.bubble_shape, "bubbleSize": lay.bubble_size,
                       "captionBar": cap_mode == "bar" and lay.mode == "split" and edl.captions.enabled},
            "zooms": zooms, "captions": {**edl.captions.model_dump(), "words": words if edl.captions.enabled else []},
            "overlays": overlays, "sfx": sfx, "transitions": trans, "hook": {**edl.hook.model_dump(), "enabled": not gta}, "music": _music_props(edl),
            "captionScale": 0.78 if gta else 1, "captionOffsetY": 80 if gta else 0, "zoomScale": 1,
            "logos": ([{"file": f"logos/{logo_slug}.png", "x": 0.82, "y": 0.105, "w": 285, "opacity": 0.94}] if logo_on else [])}


# Footage whose framing is already composed (trailers, cutscenes, montages) is left on a static crop; a virtual camera
# that follows motion only helps footage where the action wanders (gameplay, streams, commentary, tutorials).
REFRAME_TYPES = ("gameplay", "stream", "commentary", "tutorial")


def _reframe_path(rd: Path, base: Path, edl: EDL, bi: dict, area: dict | None, content_type: str | None, seg_key: str):
    """Camera path [(t, focus_x)] for the gameplay window, or None for a static crop (cached per cut + layout)."""
    if not config.REFRAME or content_type not in REFRAME_TYPES or edl.style == "cinematic":
        return None
    lay = edl.layout
    cam = lay.facecam_box.model_dump() if lay.mode in ("split", "bubble") and lay.facecam_box else None
    from . import fastrender as fr
    h_game = {"full": fr.H, "fit": round(fr.W * 1.12) // 2 * 2, "bubble": fr.H,
              "split": fr.H - round(fr.H * (0.5 if lay.mode == "split" else lay.facecam_share)) // 2 * 2}[lay.mode]
    sw, sh = bi["width"], bi["height"]
    cw = fr.cover_rect(sw, sh, fr.W, h_game, None, 0.5, area)[2]
    region = (area["x"], area["x"] + area["w"]) if area else (0.0, 1.0)
    window = cw / ((area["w"] * sw) if area else sw)
    key = hashlib_key(seg_key, cam, region, window, lay.mode)
    f = rd / "reframe.json"
    if f.exists() and json.loads(f.read_text()).get("key") == key:
        return json.loads(f.read_text())["path"] or None
    from .reframe import focus_path
    try:
        path = focus_path(base, bi["duration"], region, cam, window, prior=lay.gameplay_focus_x)
    except Exception:  # noqa: BLE001 - framing is an enhancement: never lose a clip over it
        path = []
    f.write_text(json.dumps({"key": key, "path": path}))
    return path or None


def hashlib_key(*parts) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


@stage("render")
def render(job_id, idx):
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not clip or clip["status"] not in RENDERABLE:
        raise Unrecoverable(f"clip {idx} is not ready to render (status={clip and clip['status']})")
    db.q("update clips set status='rendering' where job_id=%s and idx=%s", (job_id, idx))
    job = db.q1("select * from jobs where id=%s", (job_id,))
    edl = EDL.model_validate(clip["edl"])
    rd = config.job_dir(job_id) / f"render_{idx}"
    pub = rd / "public"
    pub.mkdir(parents=True, exist_ok=True)
    # EDL may change between attempts (re-plan): key the cache by EDL content
    import hashlib
    h = hashlib.sha256(json.dumps(clip["edl"], sort_keys=True).encode())
    for f in sorted((config.RENDERER / "src").glob("*.ts*")):  # renderer code change => re-render
        h.update(f.read_bytes())
    h.update(Path(__file__).with_name("fastrender.py").read_bytes())  # same for the fast renderer
    key = h.hexdigest()[:12]
    stamp = rd / "edl.key"
    seg_key = hashlib.sha256(json.dumps(clip["edl"]["segments"], sort_keys=True).encode()).hexdigest()[:12]
    if stamp.exists() and stamp.read_text() != key:
        (rd / "raw.mp4").unlink(missing_ok=True)
    seg_stamp = rd / "segments.key"
    if seg_stamp.exists() and seg_stamp.read_text() != seg_key:
        (pub / "base.mp4").unlink(missing_ok=True)
    stamp.write_text(key)
    seg_stamp.write_text(seg_key)
    _base(Path(job["source_path"]), edl, pub / "base.mp4", job["meta"]["source"])
    bi = media.probe(pub / "base.mp4")
    props = build_props(job_id, idx, edl, bi)
    for logo in props.get("logos") or []:
        src_logo = config.RENDERER / "public" / logo["file"]
        dst_logo = pub / logo["file"]
        if src_logo.exists() and not dst_logo.exists():
            dst_logo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_logo, dst_logo)
    if edl.layout.mode in ("split", "bubble") and edl.layout.facecam_box:
        # letterbox bars inside the stream (a trailer/cutscene playing in 2.39:1): show only the real game picture
        from .signals import _gray_frames, gameplay_area
        ga_file = rd / "gameplay_area.json"
        if not ga_file.exists() or seg_stamp.read_text() != json.loads(ga_file.read_text()).get("seg"):
            ga = gameplay_area(_gray_frames(pub / "base.mp4", bi["duration"], 16), edl.layout.facecam_box.model_dump())
            ga_file.write_text(json.dumps({"seg": seg_stamp.read_text(), "area": ga}))
        props["layout"]["gameplayArea"] = _without_cam(json.loads(ga_file.read_text())["area"], edl.layout.facecam_box.model_dump())
    props["layout"]["gameplayFocusPath"] = _reframe_path(rd, pub / "base.mp4", edl, bi, props["layout"].get("gameplayArea"),
                                                        (clip.get("candidate") or {}).get("content_type"), seg_stamp.read_text())
    if edl.layout.mode == "fit":
        _blur_bg(pub / "base.mp4", pub / "bg.mp4")
        props["bgSrc"] = "bg.mp4"
    for sfx in props["sfx"]:
        lib = config.RENDERER / "public" / sfx["file"]
        if not (pub / sfx["file"]).exists() and lib.exists():  # built-in SFX added by the renderer (cold-open whoosh)
            (pub / "sfx").mkdir(exist_ok=True)
            shutil.copy2(lib, pub / sfx["file"])
        if not (pub / sfx["file"]).exists():
            raise Unrecoverable(f"missing asset {sfx['file']} (run the assets stage)")
    for o in props["overlays"]:
        if o["file"] and not (pub / o["file"]).exists():
            raise Unrecoverable(f"missing asset {o['file']}")
    raw = rd / "raw.mp4"
    t_render = time.time()
    fallback_reason = None
    from . import fastrender
    from .manage import load_settings
    want = load_settings().render_engine
    if want == "remotion" and not config.RENDER_CPU_FALLBACK:  # GPU worker: the Chrome renderer is not installed/used
        want = "fast"
    engine = "fast" if want == "fast" and fastrender.available() else "remotion"
    if engine == "remotion" and want == "fast":  # fast was requested but cannot run here
        if not config.RENDER_CPU_FALLBACK:
            raise RuntimeError("render_failed: fast renderer unavailable (ffmpeg or fonts missing) and CPU fallback is off "
                               f"(RENDER_CPU_FALLBACK=0); ffmpeg={fastrender.FFMPEG}")
        fallback_reason = "fast renderer unavailable (ffmpeg or fonts missing)"
    new_props = json.dumps({**props, "_engine": engine}, sort_keys=True)
    old_props = (rd / "props.json").read_text() if (rd / "props.json").exists() else None
    if old_props is not None and old_props != new_props:
        raw.unlink(missing_ok=True)  # captions/timing derived differently => re-render
    (rd / "props.json").write_text(new_props)
    if not raw.exists() and engine == "fast":
        try:
            fastrender.render(props, pub, raw, rd / "fast")
        except Exception as e:  # noqa: BLE001
            if not config.RENDER_CPU_FALLBACK:  # GPU worker: fail loudly, never silently drop to the CPU renderer
                db.q("update clips set render_engine='fast', encoder=%s, render_note=%s where job_id=%s and idx=%s",
                     (fastrender.encoder(), f"render_failed: {str(e)[-400:]}", job_id, idx))
                raise RuntimeError(f"render_failed: fast renderer ({fastrender.encoder()}) failed and CPU fallback is off "
                                   f"(RENDER_CPU_FALLBACK=0): {str(e)[-600:]}") from e
            fallback_reason = str(e)[-300:]
            db.event(job_id, "warn", "render", f"CPU fallback: fast renderer failed, using Remotion: {fallback_reason}",
                     {"clip": idx, "cpu_fallback": True})
            engine = "remotion"
            (rd / "props.json").write_text(json.dumps({**props, "_engine": engine}, sort_keys=True))
    if not raw.exists():
        env = {**os.environ, "REMOTION_DISABLE_TELEMETRY": "1"}
        p = run_remotion(["render", "src/index.ts", "Clip", str(raw.with_suffix(".tmp.mp4")),
                          f"--props={rd / 'props.json'}", f"--public-dir={pub}", "--codec=h264", "--crf=17",
                          f"--concurrency={config.render_concurrency()}", "--log=warn", "--audio-codec=aac"], env, 3600)
        if p.returncode != 0:
            raise RuntimeError(f"remotion render failed: {(p.stderr or p.stdout)[-1200:]}")
        raw.with_suffix(".tmp.mp4").rename(raw)
    out_dir = config.job_dir(job_id) / "output"
    out_dir.mkdir(exist_ok=True)
    final = out_dir / f"clip_{idx:02d}.mp4"
    tmp = final.with_suffix(".tmp.mp4")
    finalize(raw, tmp)
    tmp.rename(final)
    shutil.copy2(rd / "assets_manifest.json", out_dir / f"clip_{idx:02d}_assets.json") if (rd / "assets_manifest.json").exists() else None
    encoder = fastrender.encoder() if engine == "fast" else "libx264 (Remotion, CPU)"
    note = f"CPU fallback: {fallback_reason}" if fallback_reason else ("CPU renderer selected in settings" if engine == "remotion" else None)
    secs = round(time.time() - t_render, 2)
    db.q("update clips set status='rendered', output_path=%s, render_engine=%s, encoder=%s, render_seconds=%s, render_note=%s, "
         "fail_kind=null where job_id=%s and idx=%s", (str(final), engine, encoder, secs, note, job_id, idx))
    from .queue import enqueue
    enqueue(job_id, "qc", idx, force=True)
    return {"output": str(final), "duration": props["duration"], "engine": engine, "encoder": encoder,
            "cpu_fallback": bool(fallback_reason), "render_seconds": secs}


def _without_cam(area: dict | None, box: dict) -> dict | None:
    """The webcam is shown on its own (card/bubble/split), so the gameplay window must not show it a second time:
    drop the webcam's column from the game picture when enough picture is left for a 9:16 crop."""
    a = dict(area or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
    x0, x1 = a["x"], a["x"] + a["w"]
    bx0, bx1 = box["x"], box["x"] + box["w"]
    if bx1 <= x0 or bx0 >= x1:
        return area
    if (bx0 + bx1) / 2 < 0.5:
        x0 = max(x0, bx1 + 0.005)
    else:
        x1 = min(x1, bx0 - 0.005)
    if x1 - x0 < 0.34:  # too little left: keep the full picture rather than zooming into a sliver
        return area
    return {**a, "x": round(x0, 4), "w": round(x1 - x0, 4)}
