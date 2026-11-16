"""Edit guide (dashboard Tools page): the AI does the research, the owner does the edit.
For a finished job it turns everything the pipeline found into a plain edit sheet:
  - every moment it scored (stream timestamps, why it works, hook ideas, the beats of a multi-part story);
  - per planned clip: the cut list in play order, layout (webcam rectangle in pixels), zooms, on-screen text,
    sound effects, transitions, hook, title, music mood and caption emphasis, each with its reason and the time
    both in the stream and in the rough cut;
  - exports: an unedited rough cut (the beats joined in play order, so the sheet's times line up with it), each
    beat as its own file with handles, the speech as .srt, the sheet as .csv markers and .json.
Read-only on the job: nothing is re-planned, rendered or published. Only ffmpeg runs locally for the exports."""
import csv
import io
import json
from pathlib import Path

from fastapi import HTTPException

from . import config, db, media
from .edl import EDL, to_output_time
from .main import A, app

HANDLE = 1.5  # seconds of extra footage before/after each beat file, room for the editor's own cut


def _tc(t: float | None) -> str | None:
    if t is None:
        return None
    t = max(0.0, t)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


def _job(job_id: str) -> dict:
    j = db.q1("select * from jobs where id=%s", (job_id,))
    if not j:
        raise HTTPException(404, "job not found")
    return j


def _offset(meta: dict) -> float:
    """Source second 0 is this many seconds into the original stream (only when a section was downloaded)."""
    sec = meta.get("section")
    return float(sec[0]) if sec else 0.0


def _yt(job: dict, stream_t: float) -> str | None:
    url = job.get("source_url")
    if not url:
        return None
    return f"{url}{'&' if '?' in url else '?'}t={int(stream_t)}s"


def _when(job, off, src_t, segs=None) -> dict:
    out = {"source": round(src_t, 2), "stream": _tc(src_t + off), "link": _yt(job, src_t + off)}
    if segs is not None:
        o = to_output_time(segs, src_t)
        out["cut"] = None if o is None else round(o, 2)
        out["cut_tc"] = _tc(o)
    return out


def _px_box(box, w, h) -> dict | None:
    if not box:
        return None
    b = box if isinstance(box, dict) else box.model_dump()
    return {"x": round(b["x"] * w), "y": round(b["y"] * h), "w": round(b["w"] * w), "h": round(b["h"] * h), "norm": b}


def _analysis(job_id) -> Path:
    return config.job_dir(str(job_id)) / "analysis"


def _load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


LAYOUT_TIPS = {
    "split": "Stack the webcam on top (about {share:.0%} of the height) and the gameplay below, both full width.",
    "bubble": "Gameplay full screen; webcam in a small {shape} bubble at the {corner}; grow it for the reactions.",
    "full": "One full-screen 9:16 crop of the frame, centred at {focus:.0%} across.",
    "fit": "Wide gameplay crop centred over a blurred copy of itself, so the HUD stays readable.",
}


def _clip_guide(job, idx: int, edl_raw: dict, cand: dict, w: int, h: int, off: float) -> dict:
    try:
        edl = EDL.model_validate(edl_raw)
    except Exception:  # noqa: BLE001 - an old plan that no longer validates is shown as-is, without a timeline
        return {"idx": idx, "title": edl_raw.get("title"), "error": "this clip's plan uses an older format"}
    segs = edl.segments
    lay = edl.layout
    # cut list in play order; rough-cut time of each segment
    cuts, acc = [], 0.0
    for k, s in enumerate(segs):
        cuts.append({"n": k + 1, "role": s.role, "reason": s.reason, "length": round(s.end - s.start, 2),
                     "in": _when(job, off, s.start), "out": _when(job, off, s.end),
                     "cut_start": round(acc, 2), "cut_start_tc": _tc(acc)})
        acc += s.out_len
    steps = []  # every recommendation on one time-sorted sheet

    def add(kind, t, what, why, extra=None):
        steps.append({"kind": kind, "what": what, "why": why, "at": _when(job, off, t, segs), **(extra or {})})

    for z in edl.zooms:
        tgt = {"facecam": "the webcam", "gameplay": "the gameplay", "frame": "the whole frame"}[z.target]
        add("zoom", z.start, f"{'Punch' if z.style == 'punch' else 'Smooth'} zoom {z.scale:.2f}x on {tgt} for {z.end - z.start:.1f}s"
            + (f", centred at {z.focus_x:.0%} across / {z.focus_y:.0%} down" if z.target != "facecam" else ""), z.reason,
            {"length": round(z.end - z.start, 2)})
    for o in edl.overlays:
        what = {"text": f'On-screen text "{o.value}"', "emoji": f"Emoji {o.value}", "image": f'Picture of "{o.value}"'}[o.kind]
        add("text" if o.kind == "text" else o.kind, o.start, f"{what} at the {o.position.replace('_', ' ')} for {o.end - o.start:.1f}s, {o.animation.replace('_', ' ')} in",
            o.reason, {"length": round(o.end - o.start, 2)})
    for s in edl.sfx:
        add("sfx", s.at, f"Sound effect: {s.sound.replace('_', ' ')} ({s.gain_db:.0f} dB)", s.reason)
    for t in edl.transitions:
        add("transition", t.at, {"flash": "White flash", "zoom_punch": "Zoom punch", "shake": "Screen shake"}[t.kind], t.reason)
    for c in cuts[1:]:
        steps.append({"kind": "cut", "what": f"Cut to beat {c['n']} ({c['role'].replace('_', ' ')}), {c['length']:.1f}s",
                      "why": c["reason"], "at": {**c["in"], "cut": c["cut_start"], "cut_tc": c["cut_start_tc"]}})
    steps.sort(key=lambda x: (x["at"].get("cut") is None, x["at"].get("cut") or 0))
    tip = LAYOUT_TIPS[lay.mode].format(share=lay.facecam_share, shape=lay.bubble_shape, corner=lay.bubble_corner.replace("_", " "),
                                       focus=lay.gameplay_focus_x)
    rank = _load(_analysis(job["id"]) / f"rank_{cand.get('idx', idx)}.json", {}) if cand else {}
    return {
        "idx": idx, "title": edl.title, "description": edl.description, "moment_type": edl.moment_type, "style": edl.style,
        "length": round(acc, 1), "hook": {"text": edl.hook.text, "seconds": edl.hook.duration},
        "layout": {"mode": lay.mode, "tip": tip, "webcam_px": _px_box(lay.facecam_box, w, h), "source_size": [w, h],
                   "gameplay_focus_x": lay.gameplay_focus_x, "webcam_share": lay.facecam_share,
                   "bubble": {"corner": lay.bubble_corner, "shape": lay.bubble_shape, "size": lay.bubble_size} if lay.mode == "bubble" else None},
        "captions": {"emphasis": edl.captions.emphasis, "style": edl.captions.style, "position": edl.captions.position,
                     "fixes": [f.model_dump() for f in edl.caption_fixes]},
        "music": {"enabled": edl.music.enabled, "mood": edl.music.mood, "gain_db": edl.music.gain_db},
        "cuts": cuts, "steps": steps,
        "why": {k: rank.get(k) for k in ("event", "why_engaging", "gameplay_context", "reaction_context") if rank.get(k)},
        "story": (cand or {}).get("story"),
        "pre": _pre_followed(job, idx, edl, off),
    }


def _pre_followed(job, idx, edl, off):
    """The guide the AI got before editing, each suggestion marked as used or not by the AI's plan."""
    pre = _load(_analysis(job["id"]) / f"preguide_{idx}.json", {})
    g = pre.get("guide") or {}
    if not g or g.get("error"):
        return None
    near = lambda t, items, tol: any(abs(x - t) <= tol for x in items)  # noqa: E731
    z_at = [z.start for z in edl.zooms]
    s_at = [x.at for x in edl.sfx]
    o = [(x.start, x.value.lower()) for x in edl.overlays if x.kind in ("text", "emoji")]
    kept = lambda t: to_output_time(edl.segments, t) is not None  # noqa: E731
    items = []
    for p in g.get("parts", []):
        items.append({"kind": "part", "what": f"Part {p['n']} ({p.get('role')})",
                      "why": p.get("reason") or "", "at": _when(job, off, p["start"]),
                      "used": any(sg.start < p["end"] and sg.end > p["start"] for sg in edl.segments if sg.role == "main")})
        if p.get("jump_label"):
            items.append({"kind": "text", "what": f'Time-jump label "{p["jump_label"]}"', "why": "the story jumps in time here",
                          "at": _when(job, off, p["start"]),
                          "used": any(abs(t - p["start"]) <= 3 and ("later" in v or "earlier" in v) for t, v in o)})
    if g.get("cold_open"):
        co = g["cold_open"]
        items.append({"kind": "cut", "what": "Cold-open teaser", "why": co["why"], "at": _when(job, off, co["start"]),
                      "used": any(sg.role == "cold_open" and sg.start <= co["end"] + 2 and sg.end >= co["start"] - 2 for sg in edl.segments)})
    for z in g.get("zooms", []):
        items.append({"kind": "zoom", "what": f"Zoom on the {'webcam' if z['target'] == 'facecam' else 'gameplay'}", "why": z["why"],
                      "at": _when(job, off, z["at"]), "used": near(z["at"], z_at, 2.0), "kept": kept(z["at"])})
    for f in g.get("sfx", []):
        items.append({"kind": "sfx", "what": f"Sound: {f['sound']}", "why": f["why"], "at": _when(job, off, f["at"]),
                      "used": near(f["at"], s_at, 2.0), "kept": kept(f["at"])})
    for t in g.get("texts", []):
        items.append({"kind": "text", "what": f'Text "{t["text"]}"', "why": t["why"], "at": _when(job, off, t["at"]),
                      "used": any(abs(x - t["at"]) <= 3 for x, _ in o), "kept": kept(t["at"])})
    for d in g.get("dead_air", []):
        items.append({"kind": "cut", "what": f"Cut {d['end'] - d['start']:.1f}s of dead air", "why": d["why"], "at": _when(job, off, d["start"]),
                      "used": not kept((d["start"] + d["end"]) / 2)})
    emph = {w.lower() for w in edl.captions.emphasis}
    return {"items": items, "used": sum(1 for i in items if i["used"]), "total": len(items),
            "caption_emphasis": [{"word": w, "used": w.lower() in emph} for w in g.get("caption_emphasis", [])],
            "hooks": g.get("hooks", []), "titles": g.get("titles", []), "music_mood": g.get("music_mood"),
            "guided_plan": bool(pre.get("guided_plan"))}


def _moments(job, off) -> list[dict]:
    a = _analysis(job["id"])
    out = []
    for c in _load(a / "candidates.json", []):
        r = _load(a / f"rank_{c['idx']}.json", {})
        tm = c.get("text_moment") or {}
        st = c.get("story") or {}
        beats = [{"role": b.get("role"), "reason": b.get("reason"), "quote": b.get("evidence"),
                  "start": _when(job, off, b["start"]), "end": _when(job, off, b["end"])} for b in st.get("beats", [])]
        out.append({
            "idx": c["idx"], "score": c.get("moment_score"), "ai_quality": r.get("quality"),
            "title": r.get("title") or st.get("title"), "kind": tm.get("kind"),
            "what": r.get("event") or st.get("summary") or tm.get("why"),
            "why": r.get("why_engaging") or st.get("connection") or tm.get("why"),
            "hook": r.get("hook") or tm.get("hook"), "start": _when(job, off, c["start"]), "end": _when(job, off, c["end"]),
            "length": c.get("duration"), "beats": beats, "is_highlight": r.get("is_highlight"),
        })
    out.sort(key=lambda m: -(m["score"] or 0))
    return out


@app.get("/tools/guide/{job_id}", dependencies=A)
def guide(job_id: str):
    job = _job(job_id)
    meta = job["meta"] or {}
    src = meta.get("source") or {}
    w, h, off = src.get("width") or 1920, src.get("height") or 1080, _offset(meta)
    cands = {c["idx"]: c for c in _load(_analysis(job_id) / "candidates.json", [])}
    clips = []
    for c in db.q("select idx, status, edl, candidate from clips where job_id=%s order by idx", (job_id,)):
        if not c["edl"]:
            continue
        g = _clip_guide(job, c["idx"], c["edl"], c["candidate"] or cands.get(c["idx"]) or {}, w, h, off)
        g["status"] = c["status"]
        _thumbs(job, c["idx"], g)
        g["exports"] = _exports(job_id, c["idx"])
        clips.append(g)
    info = _load(config.job_dir(str(job_id)) / "source.info.json", {})
    return {"job": {"id": job_id, "title": job["title"] or info.get("title") or meta.get("source_title"), "status": job["status"], "url": job["source_url"],
                    "engine": meta.get("engine"), "section": meta.get("section"), "stream_length": meta.get("source_total_s") or src.get("duration")},
            "clips": clips, "moments": _moments(job, off)}


def _out(job_id) -> Path:
    p = config.job_dir(str(job_id)) / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _thumbs(job, idx, g):
    """One still from the middle of each beat, so the owner sees the picture (layouts change over a long stream)."""
    src = Path(job["source_path"] or "")
    for c in g.get("cuts", []):
        f = _out(job["id"]) / f"guide_{idx:02d}_still_{c['n']:02d}.jpg"
        if not f.exists() and src.is_file():
            try:
                media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{(c['in']['source'] + c['out']['source']) / 2:.2f}",
                           "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(f)], timeout=60)
            except RuntimeError:
                continue
        c["still"] = f"/files/{job['id']}/{f.name}" if f.exists() else None


def _exports(job_id, idx) -> list[dict]:
    files = sorted(f for f in _out(job_id).glob(f"guide_{idx:02d}_*") if "_still_" not in f.name)
    return [{"name": f.name, "url": f"/files/{job_id}/{f.name}", "mb": round(f.stat().st_size / 1e6, 1)} for f in files]


def _srt(words, segs) -> str:
    """Speech captions on the rough-cut timeline (only words inside the kept segments, in play order)."""
    rows, acc = [], 0.0
    for s in segs:
        ws = [w for w in words if w["s"] >= s.start - 0.05 and w["e"] <= s.end + 0.05]
        line = []
        for wd in ws:
            line.append(wd)
            if len(line) >= 4 or wd["w"].rstrip().endswith((".", "?", "!", ",")):
                rows.append((acc + line[0]["s"] - s.start, acc + line[-1]["e"] - s.start, " ".join(x["w"] for x in line)))
                line = []
        if line:
            rows.append((acc + line[0]["s"] - s.start, acc + line[-1]["e"] - s.start, " ".join(x["w"] for x in line)))
        acc += s.out_len

    def ts(t):
        t = max(0.0, t)
        return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round(t % 1 * 1000)) % 1000:03d}"
    return "\n".join(f"{i}\n{ts(a)} --> {ts(max(b, a + 0.3))}\n{txt}\n" for i, (a, b, txt) in enumerate(rows, 1))


def _csv(g: dict) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["rough cut time", "stream time", "type", "what to do", "why"])
    wr.writerow(["0:00.0", g["cuts"][0]["in"]["stream"], "hook", f'Hook text "{g["hook"]["text"]}" for {g["hook"]["seconds"]}s', "first seconds decide if people stay"])
    wr.writerow(["0:00.0", "", "layout", g["layout"]["tip"], ""])
    for s in g["steps"]:
        wr.writerow([s["at"].get("cut_tc") or "", s["at"].get("stream") or "", s["kind"], s["what"], s["why"]])
    return buf.getvalue()


def _encode_args():
    from . import media
    return [*media.h264("master"), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


@app.post("/tools/guide/{job_id}/clips/{idx}/export", dependencies=A)
def export(job_id: str, idx: int):
    """Write the rough cut, one file per beat (with handles), speech .srt, markers .csv and the sheet .json."""
    job = _job(job_id)
    c = db.q1("select edl from clips where job_id=%s and idx=%s", (job_id, idx))
    if not c or not c["edl"]:
        raise HTTPException(404, "this clip has no plan yet")
    src = Path(job["source_path"] or "")
    if not src.is_file():
        raise HTTPException(409, "the source video was cleaned up; re-run the job to export footage")
    g = guide(job_id)
    cg = next(x for x in g["clips"] if x["idx"] == idx)
    if cg.get("error"):
        raise HTTPException(409, cg["error"])
    edl = EDL.model_validate(c["edl"])
    out, pre = _out(job_id), f"guide_{idx:02d}_"
    for f in out.glob(pre + "*"):
        if "_still_" not in f.name:
            f.unlink()
    dur = (job["meta"] or {}).get("source", {}).get("duration") or 1e9
    # rough cut: the segments in play order, untouched footage, so the sheet's "rough cut time" matches it
    args, parts = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"], []
    for k, s in enumerate(edl.segments):
        args += ["-ss", f"{s.start:.3f}", "-t", f"{s.end - s.start:.3f}", "-i", str(src)]
        parts.append(f"[{k}:v:0]setpts=PTS-STARTPTS,fps=30[v{k}];[{k}:a:0]asetpts=PTS-STARTPTS,aresample=48000[a{k}];")
    n = len(edl.segments)
    graph = "".join(parts) + "".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    media.run(args + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", *_encode_args(), str(out / f"{pre}rough_cut.mp4")], timeout=900)
    # each beat on its own with handles (main segments only; the cold open repeats footage of a later beat)
    for k, s in enumerate(edl.segments):
        if s.role != "main":
            continue
        a, b = max(0.0, s.start - HANDLE), min(dur, s.end + HANDLE)
        media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src),
                   *_encode_args(), str(out / f"{pre}beat_{k + 1:02d}.mp4")], timeout=600)
    tr = _load(_analysis(job_id) / "transcript.json", {})
    cache = _load(_analysis(job_id) / "aligned.json", {})  # {range key: [words]} written by the render's word alignment
    aligned = {round(w["s"], 2): w for v in (cache.values() if isinstance(cache, dict) else []) if isinstance(v, list) for w in v}
    words = sorted(aligned.values(), key=lambda w: w["s"]) if aligned else tr.get("words", [])
    (out / f"{pre}speech.srt").write_text(_srt(words, edl.segments))
    (out / f"{pre}markers.csv").write_text(_csv(cg))
    (out / f"{pre}sheet.json").write_text(json.dumps(cg, indent=1))
    db.event(job_id, "info", "guide", f"edit guide exported for clip {idx}", {"files": len(list(out.glob(pre + '*')))})
    return {"exports": _exports(job_id, idx)}
