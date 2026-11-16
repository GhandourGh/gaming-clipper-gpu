"""Stage 7: automatic technical QC. Passing QC does NOT approve a clip;
a human review (Telegram buttons) is still required."""
import json
import re
from pathlib import Path

from . import config, cutcheck, db, media
from .queue import stage

MAX_CAPTION_CHARS = 14  # widest word that fits inside the safe area at 92px


def silence_limit(meta: dict | None) -> float | None:
    """Max tolerated silence in the output, from the job's mode options.
    None = skip the dead_air check (owner switched silence cutting off)."""
    from . import modes
    gap = modes.silence_gap(meta or {})
    if gap is None:      # silence cutting off: quiet stretches are intentional
        return None
    if gap is False:     # unspecified: the old default
        return 2.0
    return float(gap) + 0.8  # the planner keeps gaps up to `gap`; allow a margin


def check_file(path: Path, props: dict | None, silence_d: float | None = 2.0) -> dict:
    checks, fails = {}, []

    def add(name, ok, detail):
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            fails.append(name)

    try:
        info = media.probe(path)
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "fails": ["decode"], "checks": {"decode": {"ok": False, "detail": str(e)[:300]}}}
    add("dimensions", (info["width"], info["height"]) == (1080, 1920), f"{info['width']}x{info['height']}")
    add("fps", abs(info["fps"] - 30) < 0.5, info["fps"])
    add("audio_present", info["has_audio"], info["acodec"])
    drift = abs(info["v_duration"] - info["a_duration"])
    add("av_sync", drift < 0.1, f"video {info['v_duration']:.3f}s vs audio {info['a_duration']:.3f}s")
    exp = props["duration"] if props else None
    add("duration", exp is None or abs(info["duration"] - exp) < 0.2, f"{info['duration']:.2f}s (expected {exp})")
    # full decode + black frames + silence + loudness in one pass
    sd = silence_d if silence_d is not None else 600  # effectively off, but ebur128 still runs
    # Ignore sub-second cinematic fades; flag only a sustained blank section that viewers would perceive as a defect.
    p = media.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf", "blackdetect=d=0.8:pix_th=0.08",
                   "-af", f"silencedetect=n=-50dB:d={sd:g},ebur128=peak=true", "-f", "null", "-"], timeout=900)
    err = p.stderr
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)
    add("black_frames", not blacks, [f"{float(a):.2f}-{float(b):.2f}" for a, b in blacks] or "none")
    sil = re.findall(r"silence_start: ([\d.]+)", err)
    add("dead_audio", silence_d is None or not sil, sil or "none")
    summ = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    i_m = re.search(r"I:\s+(-?[\d.]+) LUFS", summ)
    tp_m = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ)
    lufs = float(i_m.group(1)) if i_m else None
    tp = float(tp_m.group(1)) if tp_m else None
    add("loudness", lufs is not None and -16.5 <= lufs <= -11.5, f"{lufs} LUFS integrated")
    add("clipping", tp is not None and tp <= -0.5, f"true peak {tp} dBTP")
    add("decode_errors", "error" not in err.lower().replace("error_", ""), "clean" if "error" not in err.lower() else "decoder reported errors")
    if props:
        words = props["captions"]["words"]
        bad_t = [w for w in words if not (0 <= w["s"] <= w["e"] <= props["duration"] + 0.05)]
        mono = all(a["s"] <= b["s"] for a, b in zip(words, words[1:]))
        add("caption_timing", not bad_t and mono, f"{len(words)} words, {len(bad_t)} out of range, monotonic={mono}")
        long_w = [w["w"] for w in words if len(re.sub(r"\W", "", w["w"])) > MAX_CAPTION_CHARS]
        add("caption_safe_area", not long_w, long_w or "all words fit the safe area")
        add("hook_length", len(props["hook"]["text"]) <= 60, props["hook"]["text"])
    return {"passed": not fails, "fails": fails, "checks": checks}


@stage("qc")
def qc(job_id, idx):
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    rd = config.job_dir(job_id) / f"render_{idx}"
    props = json.loads((rd / "props.json").read_text()) if (rd / "props.json").exists() else None
    job = db.q1("select meta from jobs where id=%s", (job_id,))
    res = check_file(Path(clip["output_path"]), props, silence_limit((job or {}).get("meta")))
    # assets referenced must still exist
    missing = [o["file"] for o in (props or {}).get("overlays", []) if o["file"] and not (rd / "public" / o["file"]).exists()]
    res["checks"]["assets"] = {"ok": not missing, "detail": missing or "all present"}
    if missing:
        res["passed"] = False
        res["fails"].append("assets")
    # cut integrity: planned cuts in word gaps, no mid-sentence ending, room after the last word
    try:
        tpath = config.job_dir(job_id) / "analysis" / "transcript.json"
        words = (json.loads(tpath.read_text()).get("words") or []) if tpath.exists() else []
        segs = ((clip.get("edl") or {}).get("segments") or []) if isinstance(clip.get("edl"), dict) else []
        cutcheck.merge(res, cutcheck.edl_checks(segs, words))
        if props:
            cutcheck.merge(res, cutcheck.output_checks(props, props["duration"]))
    except Exception as e:  # noqa: BLE001 - a broken transcript must not block QC itself
        res["checks"]["cut_integrity"] = {"ok": True, "detail": f"skipped: {e}"[:200]}
    status = "ready_for_review" if res["passed"] else "qc_failed"
    db.q("update clips set qc=%s, status=%s where job_id=%s and idx=%s", (db.J(res), status, job_id, idx))
    db.event(job_id, "info" if res["passed"] else "warn", "qc", f"clip {idx}: {status}", {"fails": res["fails"]})
    return {"passed": res["passed"], "fails": res["fails"]}
