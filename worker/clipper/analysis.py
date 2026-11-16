"""Stage 2, pass 1: cheap local signals -> candidate windows.
Signals: loudness spikes, high-frequency energy (screams/laughter), scene-cut
density (action), speech bursts, exclamation keywords, plus facecam location.
Each artefact is cached in the job folder so a retry resumes where it stopped."""
import json
import platform
import re
import wave
from pathlib import Path

import numpy as np

from . import config, db, media
from .queue import stage

KEYWORDS = re.compile(
    r"\b(no way|oh my (god|gosh)|omg|what the|wtf|holy|let'?s go+|bro+|dude|insane|crazy|clutch|how did|"
    r"are you kidding|no+!*|yes+!*|oh no|run|help|dead|killed|wait what|i can'?t believe|unbelievable|"
    r"ha(ha)+|lol|lmao|scream\w*|laugh\w*)\b", re.I)


def _cached(path: Path, fn):
    if path.exists():
        return json.loads(path.read_text())
    v = fn()
    path.write_text(json.dumps(v))
    return v


def audio_features(wav: Path, hop=0.5) -> dict:
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sr * hop)
    frames = x[: len(x) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-10)
    spec = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / sr)
    hf = spec[:, (freqs >= 1500) & (freqs <= 6000)].sum(axis=1) / (spec.sum(axis=1) + 1e-10)
    return {"hop": hop, "rms_db": (20 * np.log10(rms)).round(2).tolist(), "hf_ratio": hf.round(4).tolist()}


def scene_cuts(src: Path) -> list[float]:
    p = media.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-an",
                   "-vf", "fps=4,scale=320:-2,select='gt(scene,0.25)',showinfo", "-f", "null", "-"], timeout=7200)
    return [round(float(m), 2) for m in re.findall(r"pts_time:([0-9.]+)", p.stderr)]


def vocabulary(job: dict, src: Path) -> str:
    """Names/terms from the title and the video metadata, given to Whisper as context (fixes misheard names)."""
    parts = [job.get("title") or ""]
    info = src.parent / "source.info.json"
    if info.exists():
        try:
            meta = json.loads(info.read_text())
            parts += [meta.get("title") or "", ", ".join((meta.get("tags") or [])[:20])]
        except (ValueError, OSError):
            pass
    text = ". ".join(p.strip() for p in parts if p and p.strip())
    return text[:400]


_FASTER_MODELS = {}
WHISPER_DEVICE = {}  # model -> "cuda float16" or "cpu int8 (CUDA failed: ...)"; shown on /ready


def _faster_model(name: str):
    """Load and cache the CUDA Whisper model used by the Linux GPU worker."""
    from faster_whisper import WhisperModel
    # Accept the existing MLX names if a shared configuration reaches Linux.
    short = name.rsplit("/", 1)[-1].replace("whisper-", "").replace("-mlx", "")
    if short not in _FASTER_MODELS:
        try:
            _FASTER_MODELS[short] = WhisperModel(short, device="cuda", compute_type="float16")
            WHISPER_DEVICE[short] = "cuda float16"
        except Exception as e:  # noqa: BLE001
            # CPU keeps non-GPU development usable, but it is never silent: /ready and the job log show it.
            WHISPER_DEVICE[short] = f"cpu int8 (CUDA failed: {str(e)[:160]})"
            db.event(None, "warn", "whisper", f"Whisper {short} running on CPU: {str(e)[:300]}", {})
            _FASTER_MODELS[short] = WhisperModel(short, device="cpu", compute_type="int8")
    return _FASTER_MODELS[short]


def _wav_array(wav: Path):
    """16 kHz mono PCM WAV -> float32 array, so faster-whisper skips its PyAV decoder (PyAV versions differ per image).
    Any other format is passed as a path."""
    import numpy as np
    try:
        with wave.open(str(wav), "rb") as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
                return str(wav)
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    except (wave.Error, OSError):
        return str(wav)


def transcribe(wav: Path, vocab: str = "", model: str | None = None) -> dict:
    chosen = model or config.WHISPER_MODEL
    if platform.system() == "Darwin":
        import mlx_whisper  # heavy import, keep lazy
        r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=chosen, word_timestamps=True,
                                   condition_on_previous_text=False, initial_prompt=vocab or None)
    else:
        segments, info = _faster_model(chosen).transcribe(
            _wav_array(wav), word_timestamps=True, condition_on_previous_text=False,
            initial_prompt=vocab or None, vad_filter=True,
        )
        r = {
            "language": info.language,
            "segments": [{
                "start": s.start, "end": s.end, "text": s.text,
                "no_speech_prob": s.no_speech_prob,
                "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])],
            } for s in segments],
        }
    words, segs = [], []
    for s in r.get("segments", []):
        if s.get("no_speech_prob", 0) > 0.8:
            continue
        segs.append({"s": round(float(s["start"]), 2), "e": round(float(s["end"]), 2), "text": s["text"].strip()})
        for w in s.get("words", []):
            t = w["word"].strip()
            if t:
                words.append({"w": t, "s": round(float(w["start"]), 2), "e": round(float(w["end"]), 2)})
    return {"language": r.get("language"), "segments": segs, "words": words}


def story_index_transcript(wav: Path, duration: float, feats: dict, cuts: list[float], face_motion,
                           vocab: str, work: Path) -> dict:
    """Fast whole-source index: full-stream signals choose distributed evidence windows, then one small-model
    transcription pass covers only those windows. Selected story beats are refined later with the large model."""
    n = max(1, int(duration))
    per = max(1, int(round(1 / feats["hop"])))
    rms = np.asarray(feats["rms_db"][:n * per], dtype=float)
    rms = np.pad(rms, (0, max(0, n * per - len(rms))), constant_values=np.median(rms) if len(rms) else 0)
    loud = _z(rms.reshape(n, per).max(axis=1))
    score = np.maximum(loud, 0)
    if face_motion:
        fm = np.asarray(face_motion[:n], dtype=float)
        fm = np.pad(fm, (0, max(0, n - len(fm))))
        score += 0.8 * np.maximum(_z(fm), 0)
    scene = np.zeros(n)
    for t in cuts:
        if 0 <= int(t) < n:
            scene[int(t)] = 1
    score += 0.25 * np.convolve(scene, np.ones(5), "same")

    # One 35 s context sample per 10 minutes plus up to 16 high-interest 75 s windows.
    ranges = [(max(0.0, t - 5), min(duration, t + 30)) for t in np.arange(30, duration, 600)]
    work_score = score.copy()
    for _ in range(min(16, max(4, int(duration // 600)))):
        p = int(np.argmax(work_score))
        ranges.append((max(0.0, p - 35), min(duration, p + 40)))
        work_score[max(0, p - 120):min(n, p + 120)] = -1e9
    ranges.sort()
    merged = []
    for s, e in ranges:
        if merged and s <= merged[-1][1] + 2:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    montage = work / "story_index.wav"
    if not montage.exists():
        parts = [f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}];" for i, (s, e) in enumerate(merged)]
        graph = "".join(parts) + "".join(f"[a{i}]" for i in range(len(merged))) + f"concat=n={len(merged)}:v=0:a=1[out]"
        media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-filter_complex", graph,
                   "-map", "[out]", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(montage)], timeout=900)
    tr = transcribe(montage, vocab, config.STORY_WHISPER_MODEL)
    offsets, acc = [], 0.0
    for s, e in merged:
        offsets.append((acc, acc + e - s, s))
        acc += e - s
    def source_time(t):
        a, b, s = next((x for x in offsets if x[0] <= t <= x[1] + 0.05), offsets[-1])
        return s + min(max(0.0, t - a), b - a)
    for group in (tr["segments"], tr["words"]):
        for item in group:
            item["s"], item["e"] = round(source_time(item["s"]), 2), round(source_time(item["e"]), 2)
    tr.update({"coarse": True, "covered": [[round(s, 2), round(e, 2)] for s, e in merged],
               "sampled": True, "source_duration": duration, "indexed_seconds": round(acc, 1),
               "index_model": config.STORY_WHISPER_MODEL})
    return tr


def refine_transcript(job_id: str, start: float, end: float) -> dict:
    """Make sure [start, end] has an accurate word-level transcript (long sources are not transcribed up front)."""
    a = config.job_dir(job_id) / "analysis"
    path = a / "transcript.json"
    tr = json.loads(path.read_text())
    if not tr.get("partial") and not tr.get("coarse"):
        return tr
    s, e = max(0.0, start - 1.0), end + 1.0
    coverage_key = "refined" if tr.get("coarse") else "covered"
    if any(cs <= s and e <= ce for cs, ce in tr.get(coverage_key, [])):
        return tr
    job = db.q1("select * from jobs where id=%s", (job_id,))
    piece = a / f"audio_{s:.0f}_{e:.0f}.wav"
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{s}", "-t", f"{e - s}", "-i", str(a / "audio.wav"),
               "-c:a", "pcm_s16le", str(piece)], timeout=300)
    part = transcribe(piece, vocabulary(job, Path(job["source_path"])))
    piece.unlink(missing_ok=True)
    shift = lambda x: {**x, "s": round(x["s"] + s, 2), "e": round(x["e"] + s, 2)}  # noqa: E731
    tr["words"] = sorted([w for w in tr["words"] if not (s <= w["s"] < e)] + [shift(w) for w in part["words"]], key=lambda w: w["s"])
    tr["segments"] = sorted([x for x in tr["segments"] if not (s <= x["s"] < e)] + [shift(x) for x in part["segments"]], key=lambda x: x["s"])
    tr["language"] = tr.get("language") or part.get("language")
    tr.setdefault(coverage_key, []).append([s, e])
    path.write_text(json.dumps(tr))
    db.event(job_id, "info", "analyze", f"transcribed clip window {s:.0f}-{e:.0f}s ({len(part['words'])} words)", {})
    return tr


def find_facecam(src: Path, duration: float, samples=36) -> dict:
    """Locate a stable face region (streamer facecam). Normalised 0-1 boxes."""
    import cv2
    cap = cv2.VideoCapture(str(src))
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    hits = []
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC, (i + 0.5) * duration / samples * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        sc = 960 / w
        g = cv2.cvtColor(cv2.resize(frame, (960, int(h * sc))), cv2.COLOR_BGR2GRAY)
        for (x, y, fw, fh) in casc.detectMultiScale(g, 1.1, 6, minSize=(36, 36)):
            hits.append((i, x / g.shape[1], y / g.shape[0], fw / g.shape[1], fh / g.shape[0]))
    cap.release()
    if not hits:
        return {"found": False, "hit_rate": 0.0}
    a = np.array(hits)
    cx, cy = a[:, 1] + a[:, 3] / 2, a[:, 2] + a[:, 4] / 2
    mx, my = np.median(cx), np.median(cy)
    near = (np.abs(cx - mx) < 0.08) & (np.abs(cy - my) < 0.10)
    rate = len(set(a[near, 0].tolist())) / samples
    if rate < 0.3:
        return {"found": False, "hit_rate": round(rate, 2)}
    fw, fh = float(np.median(a[near, 3])), float(np.median(a[near, 4]))
    fx, fy = float(np.median(cx[near])), float(np.median(cy[near]))
    bw, bh = min(1.0, fw * 3.2), min(1.0, fh * 2.8)
    x0, y0 = min(max(0.0, fx - bw / 2), 1 - bw), min(max(0.0, fy - bh * 0.42), 1 - bh)
    return {"found": True, "hit_rate": round(rate, 2), "fullscreen_face": fw > 0.22,
            "face": {"x": round(fx - fw / 2, 3), "y": round(fy - fh / 2, 3), "w": round(fw, 3), "h": round(fh, 3)},
            "box": {"x": round(x0, 3), "y": round(y0, 3), "w": round(bw, 3), "h": round(bh, 3)}}


def _z(v):
    v = np.asarray(v, dtype=np.float64)
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826 + 1e-6
    return np.clip((v - med) / mad, -3, 8)


def score_timeline(duration, feats, cuts, tr, face_motion=None, chat=None, extra=None) -> dict:
    """Per-second interest score from all signals. Returns components for transparency."""
    n = max(1, int(duration))
    per = int(round(1 / feats["hop"]))
    def per_sec(arr):
        a = np.asarray(arr[: n * per], dtype=np.float64)
        a = np.pad(a, (0, n * per - len(a)), constant_values=a.min() if len(a) else 0)
        return a.reshape(n, per).max(axis=1)
    loud = _z(per_sec(feats["rms_db"]))
    hf = _z(per_sec(feats["hf_ratio"]) * (per_sec(feats["rms_db"]) > np.median(feats["rms_db"])))
    sc = np.zeros(n)
    for c in cuts:
        if int(c) < n:
            sc[int(c)] += 1
    scene = _z(np.convolve(sc, np.ones(5), "same"))
    wps = np.zeros(n)
    for w in tr["words"]:
        if int(w["s"]) < n:
            wps[int(w["s"])] += 1
    speech = _z(np.convolve(wps, np.ones(3) / 3, "same"))
    kw = np.zeros(n)
    for s in tr["segments"]:
        k = len(KEYWORDS.findall(s["text"])) + s["text"].count("!")
        if k and int(s["s"]) < n:
            kw[int(s["s"]): min(n, int(s["e"]) + 1)] += min(k, 3)
    comp = {"loud": loud, "scream": hf, "scene": scene, "speech": speech, "keyword": np.clip(kw, 0, 4)}
    raw = 1.0 * np.maximum(loud, 0) + 0.8 * np.maximum(hf, 0) + 0.5 * np.maximum(scene, 0) \
        + 0.4 * np.maximum(speech, 0) + 0.9 * comp["keyword"]

    def fit(arr):
        a = np.asarray(arr[:n], dtype=np.float64)
        return np.pad(a, (0, n - len(a)))
    if face_motion:  # the streamer moving a lot on camera = a reaction (jump, lean in, hands on head)
        comp["reaction"] = _z(np.convolve(fit(face_motion), np.ones(2) / 2, "same"))
        raw = raw + 0.9 * np.maximum(comp["reaction"], 0)
    if chat:  # chat spikes are the strongest single signal on streams (already shifted to the on-screen moment)
        comp["chat"] = _z(np.convolve(fit(chat["rate"]), np.ones(5) / 5, "same"))
        comp["chat_hype"] = _z(np.convolve(fit(chat["hype"]), np.ones(5) / 5, "same"))
        raw = raw + 1.0 * np.maximum(comp["chat"], 0) + 1.2 * np.maximum(comp["chat_hype"], 0)
    from . import momentum
    extra = extra or {}
    # a scream right after normal talk stands out even for a streamer who is always loud
    comp["jump"] = momentum.jump_per_second(per_sec(feats["rms_db"]))
    raw = raw + 3.0 * comp["jump"]
    if extra.get("heat") is not None:  # YouTube "most replayed": viewers' own interest curve
        comp["heat"] = fit(extra["heat"])
        raw = raw + 2.5 * comp["heat"]
    if extra.get("twitch") is not None:  # moments Twitch viewers clipped themselves
        comp["viewer_clips"] = fit(extra["twitch"])
        raw = raw + 3.0 * comp["viewer_clips"]
    smooth = np.convolve(raw, np.ones(5) / 5, "same")
    ads = extra.get("ads")
    if ads is not None and len(ads) and np.any(ads):  # ad breaks never become candidates
        mask = np.zeros(n, bool)
        mask[: len(ads)] = np.asarray(ads[:n], bool)
        comp["ad"] = mask.astype(float)
        smooth[mask] = float(smooth.min()) - 1.0
    return {"score": smooth, "components": comp}


def pick_candidates(duration, tl, tr, max_n, length=None, reactions=None) -> list[dict]:
    # window around a peak: default ~32-75 s; for a target length, room to cut down to it (and to build a story)
    hi = float(length[1]) if length else 60.0
    # no target length: the AI picks 20-90 s per clip, so windows must have room for a 90 s story
    pre, post, cap = (int(hi * 0.9), int(hi * 0.5), hi * 1.8) if length else (40, 25, 130.0)
    lo_win = max(15.0, float(length[0]) + 8) if length else 15.0
    score = tl["score"].copy()
    n = len(score)
    if duration <= 75:  # short source: the whole thing is the candidate
        peaks = [int(np.argmax(score))]
        windows = [(0.0, float(duration))]
    else:
        thr = float(np.median(score) + 1.0 * np.std(score))
        # uniformly intense sources (trailers, montages) rarely clear the threshold more than once:
        # always offer about one candidate per 45 s and let pass 2 (Gemini) judge them
        min_n = min(max_n, max(1, int(duration // 45)))
        peaks, windows, work = [], [], score.copy()
        while len(peaks) < max_n:
            p = int(np.argmax(work))
            if work[p] < -1e8 or (work[p] < thr and len(peaks) >= min_n):
                break
            s, e = max(0, p - pre), min(n, p + post)
            # extend while neighbours stay interesting (payoff / follow-up reaction)
            while e < min(n, p + post + 28) and score[e:e + 4].mean() > thr * 0.8:
                e += 4
            while s > max(0, p - pre - 25) and score[max(0, s - 4):s].mean() > thr * 0.8:
                s -= 4
            # snap to sentence boundaries so we do not cut mid-sentence
            starts = [x["s"] for x in tr["segments"] if s - 6 <= x["s"] <= s + 2]
            ends = [x["e"] for x in tr["segments"] if e - 2 <= x["e"] <= e + 6]
            fs = min(starts) if starts else float(s)
            fe = max(ends) if ends else float(e)
            fe = min(float(duration), max(fe, fs + lo_win), fs + cap)
            for ws, we in windows:  # never overlap an earlier pick: the clips would repeat each other
                if fs < we and fe > ws:
                    if p >= we:
                        fs = we
                    else:
                        fe = ws
            peaks.append(p)
            windows.append((round(fs, 2), round(fe, 2)))
            work[max(0, int(fs) - 5): min(n, int(fe) + 5)] = -1e9
            if fe - fs < lo_win:  # too little room left around this peak
                peaks.pop(); windows.pop()
            if work.max() < -1e8:
                break
    out = []
    for i, (p, (s, e)) in enumerate(sorted(zip(peaks, windows), key=lambda t: t[1][0])):
        comp = {k: round(float(v[max(0, p - 2): p + 3].max()), 2) for k, v in tl["components"].items()}
        text = " ".join(x["text"] for x in tr["segments"] if x["e"] > s and x["s"] < e)
        rx = [t for t in (reactions or []) if s <= t <= e]
        out.append({"idx": i, "start": s, "end": e, "duration": round(e - s, 2), "peak_time": p, "reaction_peaks": rx,
                    "pass1_score": round(float(score[p]), 2), "signals": comp, "transcript": text[:1500]})
    return out


def make_proxy(src: Path, dst: Path, s: float, e: float):
    if dst.exists():
        return
    tmp = dst.with_suffix(".tmp.mp4")
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *media.hwdec(), "-ss", f"{s}", "-t", f"{e - s}", "-i", str(src),
               "-vf", "scale=-2:360,fps=8", *media.h264("proxy"),
               "-ac", "1", "-ar", "16000", "-b:a", "32k", "-movflags", "+faststart", str(tmp)], timeout=600)
    tmp.rename(dst)


def make_story_proxy(src: Path, dst: Path, beats: list[dict]):
    """Small review proxy containing distant story beats in narrative order."""
    if dst.exists():
        return
    tmp = dst.with_suffix(".tmp.mp4")
    parts, inputs = [], []
    for i, b in enumerate(beats):
        dur = max(0.4, float(b["end"]) - float(b["start"]))
        inputs += [*media.hwdec(), "-ss", f"{float(b['start']):.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS,scale=-2:360,fps=8[v{i}];"
                     f"[{i}:a]asetpts=PTS-STARTPTS[a{i}];")
    links = "".join(f"[v{i}][a{i}]" for i in range(len(beats)))
    graph = "".join(parts) + links + f"concat=n={len(beats)}:v=1:a=1[v][a]"
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", graph,
               "-map", "[v]", "-map", "[a]", *media.h264("proxy"),
               "-ac", "1", "-ar", "16000", "-b:a", "32k", "-movflags", "+faststart", str(tmp)], timeout=1800)
    tmp.rename(dst)


def _extra_signals(job, job_id, src: Path, dur: float, a: Path) -> dict:
    """Replay heatmap (YouTube), ad-break mask and viewer-made Twitch clips (live recordings). Each is optional and
    cached; any failure only costs that signal, never the job."""
    from . import momentum
    meta = job.get("meta") or {}
    n = max(1, int(dur))
    extra = {}
    try:
        info = momentum.load_info(src.parent)
        offset = float((meta.get("section") or [0, 0])[0] or 0)
        heat = momentum.heatmap_per_second(info, n, offset)
        if heat.max() > 0:
            extra["heat"] = heat
    except Exception as e:  # noqa: BLE001
        db.event(job_id, "warn", "analyze", f"replay heatmap unavailable: {str(e)[:120]}", {})
    live = meta.get("live") or {}
    if live or "twitch" in (job.get("source_url") or ""):
        try:
            ads = np.asarray(_cached(a / "ads.json", lambda: momentum.ad_seconds(src, n).astype(int).tolist()), bool)
            if ads.any():
                extra["ads"] = ads
                db.event(job_id, "info", "analyze", f"ad break detected: {int(ads.sum())} s excluded", {})
        except Exception as e:  # noqa: BLE001
            db.event(job_id, "warn", "analyze", f"ad detection failed: {str(e)[:120]}", {})
    if live.get("platform") == "twitch" and live.get("channel") and live.get("started_at"):
        try:
            from . import twitchclips
            arr = _cached(a / "viewer_clips.json", lambda: twitchclips.clip_signal(
                live["channel"], float(live["started_at"]), n))
            if arr and max(arr) > 0:
                extra["twitch"] = np.asarray(arr, float)
                db.event(job_id, "info", "analyze", "Twitch viewer clips used as a signal", {})
        except Exception as e:  # noqa: BLE001
            db.event(job_id, "warn", "analyze", f"Twitch clips unavailable: {str(e)[:120]}", {})
    return extra


@stage("analyze")
def analyze(job_id, _clip):
    job = db.q1("select * from jobs where id=%s", (job_id,))
    src = Path(job["source_path"])
    dur = job["meta"]["source"]["duration"]
    a = config.job_dir(job_id) / "analysis"
    a.mkdir(exist_ok=True)
    wav = a / "audio.wav"
    if not wav.exists():
        tmp = a / "audio.tmp.wav"
        media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
                   "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)], timeout=3600)
        tmp.rename(wav)
    feats = _cached(a / "audio_features.json", lambda: audio_features(wav))
    from . import signals
    def _face():
        try:
            f = signals.faces(src, dur)
            if f.get("found"):
                return f
        except Exception as e:  # noqa: BLE001 - Vision unavailable: fall back to OpenCV
            db.event(job_id, "warn", "analyze", f"Vision face detection failed: {str(e)[:150]}", {})
        return find_facecam(src, dur)
    face = _cached(a / "facecam.json", _face)
    fbox = face.get("box") if face.get("found") and not face.get("fullscreen_face") else None
    if not (a / "scene_cuts.json").exists() or (fbox and not (a / "face_motion.json").exists()):
        scan = signals.scan_video(src, dur, fbox)  # one pass: scene cuts + facecam motion
        (a / "scene_cuts.json").write_text(json.dumps(scan["cuts"]))
        (a / "face_motion.json").write_text(json.dumps(scan["face_motion"]))
    cuts = json.loads((a / "scene_cuts.json").read_text())
    fm = json.loads((a / "face_motion.json").read_text()) if (a / "face_motion.json").exists() else None
    sec = (job["meta"] or {}).get("section") or [0, 0]
    from .ingest import replay_chat_file
    chat = _cached(a / "chat.json", lambda: signals.chat_activity(replay_chat_file(src.parent), dur, float(sec[0])))
    # the whole (downloaded) video is transcribed WHILE the video is scanned: Whisper runs on the GPU, the scan on the
    # CPU. Very long files still fall back to per-clip transcription (refine_transcript).
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(1)
    story_engine = (job.get("meta") or {}).get("engine") == "stories"
    if dur > config.FULL_TRANSCRIBE_MAX_S and not story_engine:
        tr_future = None
        tr = _cached(a / "transcript.json", lambda: {"language": None, "segments": [], "words": [], "partial": True, "covered": []})
    else:
        def _full_transcript():
            if story_engine and dur > 1200:
                return story_index_transcript(wav, dur, feats, cuts, fm, vocabulary(job, src), a)
            result = transcribe(wav, vocabulary(job, src), config.STORY_WHISPER_MODEL if story_engine else None)
            if story_engine:
                result.update({"coarse": True, "covered": [], "index_model": config.STORY_WHISPER_MODEL})
            return result
        tr_future = pool.submit(_cached, a / "transcript.json", _full_transcript)
    if tr_future is not None:
        tr = tr_future.result()
    pool.shutdown()
    extra = _extra_signals(job, job_id, src, dur, a)
    tl = score_timeline(dur, feats, cuts, tr, fm, chat, extra)
    reactions = signals.peaks(fm, 3.0) if fm else []
    (a / "timeline.json").write_text(json.dumps({"score": np.round(tl["score"], 3).tolist(), "reactions": reactions,
                                                 "components": {k: np.round(np.asarray(v, dtype=float), 2).tolist() for k, v in tl["components"].items()}}))
    cands = pick_candidates(dur, tl, tr, config.MAX_CANDIDATES, (job["meta"] or {}).get("length"), reactions)
    for c in cands:
        make_proxy(src, a / f"cand_{c['idx']}.mp4", c["start"], c["end"])
    (a / "candidates.json").write_text(json.dumps(cands, indent=1))
    db.set_job(job_id, status="analyzed")
    return {"candidates": len(cands), "words": len(tr["words"]), "scene_cuts": len(cuts),
            "facecam": face, "language": tr.get("language"), "reactions": len(reactions),
            "chat_messages": (chat or {}).get("messages", 0)}
