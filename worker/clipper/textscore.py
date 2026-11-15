"""Transcript-first moment scoring (the approach of the popular open-source clippers, adapted to streams):
1) the WHOLE downloaded section is transcribed during analysis;
2) one cheap text-only Gemini call reads it, with the local signal peaks as hints, and scores moments 0-100;
3) that score is fused with the local signals (audio excitement, screams, scene cuts, on-camera reactions, chat),
   each normalised within this video, into one 0-100 moment score per second;
4) candidates are re-picked on the fused score, so the paid video judging only sees the best windows.
Cost: one flash-lite text call (~$0.002-0.005 for 15-40 min of stream)."""
import json

import numpy as np

from . import config, db

TEXT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"moments": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}, "score": {"type": "INTEGER"},
        "kind": {"type": "STRING"}, "why": {"type": "STRING"}, "hook": {"type": "STRING"}},
        "required": ["start", "end", "score", "kind", "why", "hook"]}}},
    "required": ["moments"],
}

STORY_SCHEMA = {
    "type": "OBJECT",
    "properties": {"stories": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "title": {"type": "STRING"}, "hook": {"type": "STRING"}, "summary": {"type": "STRING"},
        "score": {"type": "INTEGER"}, "connection": {"type": "STRING"},
        "beats": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
            "role": {"type": "STRING", "enum": ["hook", "setup", "development", "payoff"]},
            "reason": {"type": "STRING"}, "evidence": {"type": "STRING"}},
            "required": ["start", "end", "role", "reason", "evidence"]}}},
        "required": ["title", "hook", "summary", "score", "connection", "beats"]}}},
    "required": ["stories"],
}

TEXT_PROMPT = """You are the best short-form clipper for streamers (TikTok / YouTube Shorts / Reels).
Below is the timestamped transcript of {minutes:.0f} minutes of a live stream{title}. Gameplay or on-screen action is
NOT in the transcript, so also use the signal hints: seconds where the audio got loud, someone screamed or laughed,
or the streamer moved a lot on camera.

Find up to 15 moments that would each make a great STANDALONE 20-60 s short. Great = a clear setup, a payoff and the
reaction after it: a funny line or roast, rage or a meltdown, a clutch or a fail, a big surprise or reveal, an argument,
a wholesome or absurd moment, a quotable line. Skip filler, reading donations, menus, waiting, and anything that needs
the rest of the stream to make sense.

For each moment return: start and end in seconds (as written in the transcript, covering setup -> payoff -> reaction),
score 0-100 (be strict: 50 = an average moment, 80+ = clearly clip-worthy, 90+ = could go viral), kind (funny, rage,
clutch, fail, surprise, argument, wholesome, quote, other), why (one concrete sentence), hook (max 8 words, on-screen
hook text that sets up the payoff without spoiling it).

Signal hints (second: what was detected): {hints}

Transcript:
{transcript}

Return JSON only."""

STORY_PROMPT = """You are a senior documentary and short-form story editor. Read the timestamped evidence index sampled
across the complete {minutes:.0f}-minute video{title}. Full-stream audio, scene and face-reaction signals selected the most
promising regions plus distributed chapter samples. Build at most {count} truthful, self-contained stories. A story may connect moments
hours apart when they are genuinely related: a prediction and outcome, setup and callback, question and answer, conflict
and resolution, or event and later reaction. Do not group moments merely because they share energy or a broad topic.

Each story needs a hook, setup, development when useful, and payoff. Choose each story's length from what it needs,
anywhere from 20 to 90 seconds, and VARY it: a quick punchline 20-30 s, a normal moment 40-60 s, a vlog/IRL story with a
real build-up 60-90 s. Include the build-up and the reaction, not only the punchline: a viewer who never saw the video
must understand it. Beats are usually 8-30 s each. Never add filler to hit a duration. A beat's timestamps must cover
complete spoken thoughts and must be copied from the transcript. A 20-minute vlog usually holds 3-6 clip-worthy stories:
find them all (up to the maximum above), but return fewer when fewer are strong. Reordering is allowed only when it remains truthful. `connection`
must explain exactly why all beats belong together. `evidence` must quote or closely identify the transcript evidence.
Score 0-100 for hook, clarity, entertainment, emotional impact and payoff to a viewer who has not watched the source.
If the source contains fewer strong stories, return fewer. Never fabricate visual events not supported by the transcript.

Local signal hints (use only as supporting evidence): {hints}

Complete transcript:
{transcript}

Return JSON only."""


def _ts(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _paths(job_id):
    a = config.job_dir(job_id) / "analysis"
    return a, a / "transcript.json", a / "timeline.json", a / "text_score.json"


def request(job_id: str) -> dict:
    """{"skip": reason} or {"model", "body"} for one text-only Gemini call."""
    from . import budget
    from .manage import load_settings
    if not load_settings().transcript_scoring:
        return {"skip": "transcript scoring is off in Settings"}
    a, tp, tlp, out = _paths(job_id)
    if out.exists():
        return {"skip": "already scored"}
    if not tp.exists() or not tlp.exists():
        return {"skip": "no analysis yet"}
    tr = json.loads(tp.read_text())
    if tr.get("partial") or len(tr.get("words", [])) < 40:
        return {"skip": "not enough speech for transcript scoring"}
    why = budget.blocked(job_id, budget.EST["textscore"])
    if why:
        return {"skip": why}
    tl = json.loads(tlp.read_text())
    job = db.q1("select title, meta from jobs where id=%s", (job_id,))
    lines = [f"[{x['s']:.0f}-{x['e']:.0f}] {x['text']}" for x in tr["segments"] if x["text"].strip()]
    transcript = "\n".join(lines)[:120_000]
    score = np.asarray(tl["score"])
    top = sorted(np.argsort(-score)[:25].tolist())
    comps = tl["components"]
    hints = ", ".join(f"{t}s: " + "+".join(k for k in ("loud", "scream", "reaction", "chat_hype", "scene")
                                         if k in comps and t < len(comps[k]) and comps[k][t] > 1.5) for t in top) or "none"
    meta = job.get("meta") or {}
    story_mode = meta.get("engine") == "stories"
    if story_mode:
        prompt = STORY_PROMPT.format(minutes=len(score) / 60, title=f' titled "{job["title"]}"' if job.get("title") else "",
                                     count=meta.get("clips") or 6, hints=hints, transcript="\n".join(lines)[:450_000])
        from . import modes
        prompt += modes.story_rule(meta)
        schema = STORY_SCHEMA
    else:
        prompt = TEXT_PROMPT.format(minutes=len(score) / 60, title=f' titled "{job["title"]}"' if job.get("title") else "",
                                    hints=hints, transcript=transcript)
        schema = TEXT_SCHEMA
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": schema,
                                 "maxOutputTokens": 8192}}
    return {"model": config.SCREEN_MODEL, "body": body, "mode": "stories" if story_mode else "moments"}


def _story_candidates(stories: list[dict], duration: float, tr: dict) -> list[dict]:
    """Convert evidence-backed story plans into the existing candidate contract."""
    out = []
    for story in stories:
        beats = []
        for b in story.get("beats", []):
            try:
                s, e = max(0.0, float(b["start"])), min(duration, float(b["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            if e - s < 0.4 or b.get("role") not in ("hook", "setup", "development", "payoff"):
                continue
            beats.append({**b, "start": round(s, 2), "end": round(e, 2)})
        total = sum(b["end"] - b["start"] for b in beats)
        roles = {b["role"] for b in beats}
        if not beats or "payoff" not in roles or not 8 <= total <= 120:
            continue
        s, e = min(b["start"] for b in beats), max(b["end"] for b in beats)
        text = " ".join(x["text"] for x in tr["segments"] if any(x["e"] > b["start"] and x["s"] < b["end"] for b in beats))
        out.append({"idx": len(out), "start": s, "end": e, "duration": round(total, 2),
                    "peak_time": next((b["start"] for b in beats if b["role"] == "payoff"), s),
                    "pass1_score": float(story.get("score", 0)) / 10, "moment_score": int(story.get("score", 0)),
                    "signals": {}, "transcript": text[:4000], "story": {**story, "beats": beats},
                    "text_moment": {"score": int(story.get("score", 0)), "kind": "story",
                                    "why": story.get("connection", "Connected story beats"), "hook": story.get("hook", "")}})
    return out


def fuse(signal_score, moments: list[dict], n: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-second 0-100 moment score = 45% local signals (normalised within this video) + 55% transcript score."""
    sig = np.asarray(signal_score, dtype=np.float64)[:n]
    sig = np.pad(sig, (0, max(0, n - len(sig))))
    lo, hi = float(np.min(sig)), float(np.percentile(sig, 99)) if len(sig) else 1.0
    s01 = np.clip((sig - lo) / (hi - lo + 1e-9), 0, 1)
    text = np.zeros(n)
    for m in moments:
        a, b = max(0, int(m["start"])), min(n, int(m["end"]) + 1)
        if b > a:
            text[a:b] = np.maximum(text[a:b], max(0, min(100, m["score"])) / 100)
    fused = 100 * (0.45 * s01 + 0.55 * text)
    return np.convolve(fused, np.ones(3) / 3, "same"), text


def save(job_id: str, resp: dict) -> dict:
    """Store the moments, fuse, and re-pick the candidate windows (new proxies) before the video ranking starts."""
    from .analysis import make_proxy, pick_candidates
    from .gemini import extract_json, record_cost
    record_cost(job_id, "textscore", resp, config.SCREEN_MODEL)
    a, tp, tlp, out = _paths(job_id)
    try:
        data = extract_json(resp)
        stories = data.get("stories")
        moments = [m for m in data.get("moments", []) if m["end"] > m["start"]]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        db.event(job_id, "warn", "textscore", f"transcript scoring unusable, keeping signal-only candidates: {str(e)[:150]}", {})
        out.write_text(json.dumps({"moments": [], "error": str(e)[:200]}))
        return {"used": False}
    tr = json.loads(tp.read_text())
    tl = json.loads(tlp.read_text())
    n = len(tl["score"])
    cands = []
    if stories is not None:
        cands = _story_candidates(stories, float(n), tr)
        if not cands:  # little speech (gameplay) or nothing connectable: fall back to the audio / scene / reaction signals
            db.event(job_id, "warn", "story", "no usable stories from the transcript; falling back to signal-based moments", {"stories_returned": len(stories)})
            stories = None
            moments = []
    if stories is not None:
        out.write_text(json.dumps({"stories": stories, "candidates": len(cands)}, indent=1))
        (a / "candidates.json").write_text(json.dumps(cands, indent=1))
        from .analysis import make_story_proxy
        from pathlib import Path
        src = Path(db.q1("select source_path from jobs where id=%s", (job_id,))["source_path"])
        for f in a.glob("cand_*.mp4"):
            f.unlink()
        for c in cands:
            make_story_proxy(src, a / f"cand_{c['idx']}.mp4", c["story"]["beats"])
        db.event(job_id, "info", "story", f"planned {len(cands)} connected stories", {"stories": [{"title": c["story"].get("title"), "beats": len(c["story"]["beats"])} for c in cands]})
        return {"used": True, "mode": "stories", "stories": len(cands)}
    fused, text = fuse(tl["score"], moments, n)
    comps = {k: np.asarray(v) for k, v in tl["components"].items()}
    comps["transcript"] = text * 10
    job = db.q1("select * from jobs where id=%s", (job_id,))
    meta = job["meta"] or {}
    cands = pick_candidates(float(n), {"score": fused, "components": comps}, tr, config.MAX_CANDIDATES, meta.get("length"),
                            tl.get("reactions") or [])
    for c in cands:
        p = c["peak_time"]
        c["moment_score"] = int(round(float(fused[max(0, p - 2): p + 3].max())))
        over = [m for m in moments if m["end"] > c["start"] and m["start"] < c["end"]]
        if over:
            best = max(over, key=lambda m: m["score"])
            c["text_moment"] = {k: best[k] for k in ("score", "kind", "why", "hook")}
    cands.sort(key=lambda c: -c["moment_score"])
    for i, c in enumerate(sorted(cands, key=lambda c: c["start"])):
        c["idx"] = i
    cands.sort(key=lambda c: c["idx"])
    src = job["source_path"]
    for f in a.glob("cand_*.mp4"):
        f.unlink()
    for f in a.glob("rank_*.json"):
        f.unlink()
    from pathlib import Path
    for c in cands:
        make_proxy(Path(src), a / f"cand_{c['idx']}.mp4", c["start"], c["end"])
    (a / "candidates.json").write_text(json.dumps(cands, indent=1))
    out.write_text(json.dumps({"moments": moments}, indent=1))
    top = sorted(cands, key=lambda c: -c["moment_score"])[:3]
    db.event(job_id, "info", "textscore", f"{len(moments)} moments scored from the transcript; {len(cands)} candidates, top "
             + ", ".join(f"{_ts(c['start'])} ({c['moment_score']})" for c in top), {})
    return {"used": True, "moments": len(moments), "candidates": len(cands)}
