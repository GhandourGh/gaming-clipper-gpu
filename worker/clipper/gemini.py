"""Builds Gemini request bodies and parses responses. n8n makes the actual HTTP
call (it owns the API credential), so prompts and schemas stay versioned here."""
import base64
import json
import re
from pathlib import Path

from pydantic import ValidationError

from . import config, db
from .edl import EDL, check

RANK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_highlight": {"type": "BOOLEAN"},
        "event": {"type": "STRING"}, "why_engaging": {"type": "STRING"},
        "hook": {"type": "STRING"}, "title": {"type": "STRING"},
        "quality": {"type": "NUMBER"}, "confidence": {"type": "NUMBER"},
        "best_start": {"type": "NUMBER"}, "best_end": {"type": "NUMBER"},
        "gameplay_context": {"type": "STRING"}, "reaction_context": {"type": "STRING"},
        "has_facecam": {"type": "BOOLEAN"},
        "facecam_box": {"type": "OBJECT", "nullable": True, "properties": {
            "x": {"type": "NUMBER"}, "y": {"type": "NUMBER"}, "w": {"type": "NUMBER"}, "h": {"type": "NUMBER"}},
            "required": ["x", "y", "w", "h"]},
        "content_type": {"type": "STRING", "enum": ["stream", "commentary", "gameplay", "cinematic", "montage", "tutorial"]},
    },
    "required": ["content_type", "is_highlight", "event", "why_engaging", "hook", "title", "quality", "confidence",
                 "best_start", "best_end", "gameplay_context", "reaction_context", "has_facecam"],
}

RANK_PROMPT = """You are a senior short-form gaming editor reviewing a candidate moment from a longer gaming video.
Watch and LISTEN to the attached clip ({dur:.0f}s). A cheap detector flagged it because of: {signals}.
Speech transcript (may be empty or imperfect): "{transcript}"

First decide content_type, then judge by THAT type's standard (do not punish a trailer for having no streamer reaction):
- stream: live streamer with facecam/voice reacting. Value = the moment + the reaction. High "chat"/"chat_hype" signals mean live chat exploded here; a high "reaction" signal means the streamer moved a lot on camera. Make best_end include the full reaction, not just the moment.
- commentary: edited video with a narrator/YouTuber voice. Value = the line/joke/insight + what is on screen.
- gameplay: raw play, no commentary. Value = skill, chaos, surprise, a fail, a spectacle.
- cinematic: trailer, cutscene, scripted story. Value = a memorable line, a reveal, a striking visual, a story beat that makes people want more.
- montage: already-edited highlight reel. Value = the single strongest beat in it.
- tutorial: tips/guide. Value = one clear, useful tip shown on screen.

Judge it honestly as a standalone vertical short. Many flagged moments are NOT good; say so.
- is_highlight: true only if a viewer with no context would understand and enjoy it.
- event: what actually happens (gameplay + reaction), 1-2 sentences.
- why_engaging: the concrete reason (funny fail, clutch, jump scare, big reaction, surprise...). No hype.
- hook: on-screen hook text, max 8 words, that sets up the payoff without spoiling it.
- title: short title for the clip.
- quality: 0-10 editorial quality for ranking (5 = average filler, 8+ = genuinely strong). This is a ranking aid, not a virality prediction.
- confidence: 0-1, how sure you are about your reading of the clip.
- best_start / best_end: seconds WITHIN THIS CLIP (0 to {dur:.0f}) covering the whole story: setup, escalation, payoff and reaction. {length_rule} Do not cut mid-sentence.
- gameplay_context / reaction_context: what is on screen; what the player says/does.
- has_facecam: true if a streamer webcam is visible.
- facecam_box: if has_facecam, the webcam rectangle as fractions of the frame (x, y = top-left corner, w, h = size, all 0-1), e.g. a cam in the top-left quarter is about {{"x":0,"y":0,"w":0.27,"h":0.27}}. Otherwise null.
Return JSON only."""

DIRECTOR_PROMPT = """You are an experienced short-form gaming video editor (the Creative Director).
Plan the edit of ONE vertical 9:16 clip from the attached footage. Output an Edit Decision List (EDL) as JSON.

## The moment
{event}
Why it works: {why}
Suggested hook: {hook}

## Timing
The attached video starts at source time {start:.2f}s and ends at {end:.2f}s.
ALL times in your EDL are ABSOLUTE SOURCE SECONDS: (time you see in the attached video) + {start:.2f}.
Valid range: {start:.2f} to {end:.2f}.

## Word-level transcript (source seconds)
{words}

## Detected silences / dead air (source seconds, candidates for cuts)
{silences}

## Facecam detection
{facecam}

{burned}{trends}## Source type: {content_type}
{content_guide}

## Editing style: {style_label} (set "style": "{style}")
{style_guide}
Hard limits for this style (the EDL is rejected if exceeded): {style_limits}

{moment_strategy}

## Story structure (the most important rule: a clip is a mini-story, NOT a random excerpt)
Target total length: {length_rule}
1. HOOK, first 1-3 s: open on the most striking moment, never on calm setup. Best tool: a COLD OPEN, a 1-3.5 s segment
   with role "cold_open" placed FIRST that teases the payoff (the crash, the scream, the punchline line) and cuts away
   right before it resolves. It may repeat footage that appears again later. Pair it with hook text that creates a question
   ("He did NOT see this coming"). Alternative hook: start on the strongest line someone says.
2. CONTEXT: then jump back to where the story starts. Only what is needed to understand the payoff; cut setup hard.
3. ESCALATION: build tension. You MAY reorder main segments when it tells the story better (e.g. put a reaction line right
   after the event it reacts to), but never change what actually happened.
4. PAYOFF: the big moment in full, with the reaction. This is where the strongest zoom/SFX go.
5. END on the reaction or punchline (or a beat that loops back to the start). No trailing calm, no fade-out.
Before writing the EDL, decide in `title`/`description` what the story is in one sentence; every segment must serve it.

## Editing principles (follow strictly)
- Every decision needs a purpose, written in `reason`. No effects at fixed intervals. If nothing justifies an effect, do not add it. A clean clip beats a busy one.
- segments: the ranges to KEEP, in PLAY ORDER (the list order is the edit). Optional cold_open first, then main segments.
  Cut dead air, loading, repeated words and pauses > ~0.8s, but never cut inside a word. Main segments must not overlap.
- layout: the output is a full 1080x1920 screen, so by default FILL it. Facecam detected or clearly visible => choose between "split" (facecam_box = the webcam, facecam_share about 0.22: webcam on the top ~20%, gameplay fills the other ~80%; best when the streamer talks/reacts a lot and the face IS the story) and "bubble" (gameplay fills the whole screen and the webcam sits in a small corner bubble: best when the gameplay is the story and the streamer reacts to it; pick bubble_corner on the side away from the action/HUD, bubble_shape circle for a face close-up, rounded when the cam shows more). The streamer must stay visible the whole clip but never be the centre of attention. No facecam => "full" (full-bleed crop, no bars, no empty space above or below): this is the default for cinematic, gameplay and montage sources. Use "fit" (wide card over a blurred background) ONLY when the key information is spread across the full width of the frame (HUD, scoreboard, wide scene) and a crop would hide it. Set gameplay_focus_x to where the action is (0=left, 1=right).
- zooms: punch in on the payoff, a reaction, or a detail the viewer must see. Usually 1-4 per clip. target "facecam" only in split or bubble layout: put one on the streamer's biggest reaction (a "smooth" zoom of 1.3-1.6 for 1.5-3 s; in bubble mode the bubble grows, then shrinks back) - zoom, never cut away from the gameplay for it. focus_x/focus_y are positions inside the target (0-1).
- captions: word-synced captions come from the transcript automatically. Choose style and list `emphasis` words (exact words from the transcript) worth highlighting. Disable only if there is no speech.
- overlays: do NOT use emoji. Use a very short text overlay only when it explains a beat that the footage cannot; otherwise use no overlay. kind=image only for generic, clearly licensable things; never copyrighted characters, logos or memes.
- sfx: allowed sounds: {sfx_list}. Place exactly on the beat they support; pick the sound that matches what happens on screen.
- transitions: flash / zoom_punch / shake at a specific instant, only for impact.
- music: a licensed background track is added automatically and ducked under speech. Set enabled=true and a one-word
  mood (tense, epic, upbeat, funny, chill, dark, action, sad, hype) that fits the moment. Set enabled=false when the
  source already has loud music, or when silence is part of the effect.
- hook: on-screen text for the first seconds, max 8 words, no spoilers. title + description for posting.
- tiktok_caption: recommend one short, natural TikTok caption that creates curiosity about this exact moment. No emoji,
  no hashtags inside the caption, no generic AI wording, and do not spoil the payoff.
- hashtags: exactly five relevant TikTok hashtags. Mix specific subject/game/person tags with broader gaming discovery tags;
  never use more or fewer than five and do not repeat a tag.
- {hook_rules}
- {playbook}
- Opening: the first frame must already show something (no black frames, no fade-in, no logo). Start on action or on the first word of the line.
- caption_fixes: the transcript comes from speech recognition and can mishear names, game terms and lyrics. If you can tell from the video what was really said (e.g. "Hamlu nation" is clearly wrong), add {{"wrong": "hamlu nation", "right": "..."}}. Only fix what you are sure about; never rewrite real speech.

## Output
Return ONLY a JSON object that validates against this JSON Schema:
{schema}
{feedback}{repair}"""


CONTENT_GUIDE = {
    "stream": "Live streamer footage. The reaction is the payoff: keep the moment AND the reaction after it (do not cut the clip off before the streamer reacts), keep the facecam visible the whole time (split or bubble layout), and put a facecam zoom on the biggest reaction. Chat-hype and on-camera reaction times are listed above when detected.",
    "commentary": "Narrated video. The voice line carries the clip: cut to the line, captions are essential, effects only to underline the punchline.",
    "gameplay": "Raw gameplay without commentary. Sell the action itself: tight cuts, punch-ins on the decisive moment, impact SFX on hits.",
    "cinematic": ("Scripted trailer/cutscene footage, no player. Let the visuals and the lines breathe: cut on line ends and scene changes, "
                  "few or no emoji, no SFX over dialogue, zoom only to reveal a detail. The hook teases the story, not 'gameplay'."),
    "montage": "Already-edited reel. Keep its rhythm, avoid stacking extra effects on top of existing ones; tighten to the strongest beats.",
    "tutorial": "Tip/guide. Clarity first: one tip, captions and a text overlay naming the tip, zoom on the UI element that matters.",
}


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _body(video: Path, prompt: str, schema=None, temperature=0.3, fps: float | None = None, low_res: bool = False,
          max_out: int = 8192) -> dict:
    # thinking tokens count against maxOutputTokens: long prompts (a redo with the reviewer's notes) need room
    gen = {"temperature": temperature, "responseMimeType": "application/json", "maxOutputTokens": max_out}
    if schema:
        gen["responseSchema"] = schema
    if low_res:
        gen["mediaResolution"] = "MEDIA_RESOLUTION_LOW"  # ~4x fewer tokens per frame; lets us afford more frames
    part = {"inline_data": {"mime_type": "video/mp4", "data": _b64(video)}}
    if fps:
        part["video_metadata"] = {"fps": fps}  # default sampling is 1 frame/s: too coarse for fast gameplay
    return {"contents": [{"role": "user", "parts": [part, {"text": prompt}]}], "generationConfig": gen}


def _analysis(job_id) -> Path:
    return config.job_dir(job_id) / "analysis"


def candidates(job_id) -> list[dict]:
    return json.loads((_analysis(job_id) / "candidates.json").read_text())


def _rank_prompt(job_id, c) -> str:
    sig = ", ".join(f"{k}={v}" for k, v in c["signals"].items() if v > 0.5) or "weak signals"
    meta = db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {}
    length = meta.get("length")
    length_rule = (f"The final clip will be {length[0]}-{length[1]} s after cutting, so keep at least {length[0] + 10} s of footage."
                   if length else "Choose the length the story needs (anything from 20 s to 90 s): a quick punchline can be "
                   "20-30 s, a full vlog moment with setup and payoff 60-90 s. Never cut a complete story short to make it brief.")
    if meta.get("clip_strategy") == "gta_launch_mix":
        length_rule = ("This job tests three formats. Decide whether this moment is strongest as a fast 20-35 s viral cut, a "
                       "complete 60-80 s mini-story, or an exceptional 80-120 s extended story. Preserve enough setup, action, "
                       "payoff and reaction for a story only when it earns the length; never pad weak footage.")
    tm = c.get("text_moment")
    if tm:  # what the transcript reader found here (one hint among the signals, not the verdict)
        sig += f". Transcript reader scored this moment {tm['score']}/100 ({tm['kind']}): {tm['why']}"
    if c.get("moment_score") is not None:
        sig += f". Fused moment score {c['moment_score']}/100"
    if c.get("story"):
        st = c["story"]
        beats = "; ".join(f"{b['role']} {b['start']:.1f}-{b['end']:.1f}: {b['reason']}" for b in st["beats"])
        sig += (f". This is a proposed multi-timestamp story titled '{st.get('title', '')}'. Connection: "
                f"{st.get('connection', '')}. The attached proxy concatenates these beats in story order: {beats}. "
                "Judge the complete connected story, not each cut in isolation")
    return RANK_PROMPT.format(length_rule=length_rule, dur=c["duration"], signals=sig,
                              transcript=(c["transcript"][:1200].replace('"', "'") or "(not transcribed: listen to the audio)"))


def _proxy(job_id, c) -> Path:
    proxy = _analysis(job_id) / f"cand_{c['idx']}.mp4"
    if not proxy.exists():  # proxies are deleted when a job finishes; rebuild on resubmit
        from .analysis import make_proxy
        src = db.q1("select source_path from jobs where id=%s", (job_id,))["source_path"]
        make_proxy(Path(src), proxy, c["start"], c["end"])
    return proxy


def rank_request(job_id, idx: int, pass_: str = "screen") -> dict:
    """The single ranking request for one candidate (used to rebuild it for the LLM fallback)."""
    c = next(x for x in candidates(job_id) if x["idx"] == idx)
    if pass_ == "judge":
        return {"idx": idx, "pass": "judge", "model": config.MAIN_MODEL,
                "body": _body(_proxy(job_id, c), _rank_prompt(job_id, c), RANK_SCHEMA, 0.2, fps=2, low_res=True)}
    return {"idx": idx, "pass": "screen", "model": config.SCREEN_MODEL, "body": _body(_proxy(job_id, c), _rank_prompt(job_id, c), RANK_SCHEMA, 0.2)}


def rank_requests(job_id) -> list[dict]:
    """Pass 1 (screen): cheap model ranks every candidate. Pass 2 (judge): the main model re-watches the shortlist
    at 2 fps. Returns [{"done": true}] when nothing is left, so the n8n loop can end without an empty list."""
    from . import budget
    a = _analysis(job_id)
    cands = candidates(job_id)
    todo = [c for c in cands if not (a / f"rank_{c['idx']}.json").exists()]
    if todo:
        why = budget.blocked(job_id, budget.EST["screen"] * len(todo) + budget.EST["judge"] * min(config.JUDGE_TOP, len(cands)) + budget.PER_CLIP)
        if why:
            db.event(job_id, "error", "rank", why, {})
            (a / "budget_block.txt").write_text(why)
            return [{"done": True, "reason": why}]
        return [{"idx": c["idx"], "pass": "screen", "model": config.SCREEN_MODEL,
                 "body": _body(_proxy(job_id, c), _rank_prompt(job_id, c), RANK_SCHEMA, 0.2)} for c in todo]
    ranks = [json.loads(p.read_text()) for p in a.glob("rank_*.json")]
    if config.MAIN_MODEL == config.SCREEN_MODEL or config.JUDGE_TOP <= 0:
        return [{"done": True}]
    short = sorted((r for r in ranks if r.get("is_highlight") or r.get("quality", 0) >= config.MIN_QUALITY - 1),
                   key=lambda r: (r.get("quality", 0), r.get("confidence", 0)), reverse=True)[:config.JUDGE_TOP]
    todo = [r for r in short if not r.get("judged") and not r.get("judge_failed")]
    if not todo:
        return [{"done": True}]
    if budget.blocked(job_id, budget.EST["judge"] * len(todo) + budget.PER_CLIP):
        db.event(job_id, "warn", "rank", "budget too low for the second opinion; using the first ranking", {})
        return [{"done": True}]
    by_idx = {c["idx"]: c for c in cands}
    return [{"idx": r["idx"], "pass": "judge", "model": config.MAIN_MODEL,
             "body": _body(_proxy(job_id, by_idx[r["idx"]]), _rank_prompt(job_id, by_idx[r["idx"]]), RANK_SCHEMA, 0.2, fps=2, low_res=True)}
            for r in todo]


def extract_json(resp: dict):
    """Pull the JSON object out of a generateContent response and record cost."""
    try:
        text = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Gemini response has no text (blocked or empty): {json.dumps(resp)[:300]}") from e
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def record_cost(job_id, item, resp: dict, model: str | None = None):
    u = resp.get("usageMetadata", {}) if isinstance(resp, dict) else {}
    tin, tout = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    model = (resp.get("modelVersion") if isinstance(resp, dict) else None) or model or config.MAIN_MODEL
    if isinstance(resp, dict) and resp.get("costUsd") is not None:  # fallback providers report (or estimate) the cost
        usd = float(resp["costUsd"])
    else:
        pin, pout = config.price(model)
        usd = tin / 1e6 * pin + tout / 1e6 * pout
        if config.FREE_TIER and model.split("-preview")[0] in config.FREE_TIER_MODELS:
            db.add_cost(job_id, item, {"in": tin, "out": tout, "model": model, "free_tier": True, "est_usd": round(usd, 6)}, 0)
            return
    db.add_cost(job_id, item, {"in": tin, "out": tout, "model": model}, round(usd, 6))


def save_rank(job_id, idx: int, resp: dict, pass_: str = "screen") -> dict:
    path = _analysis(job_id) / f"rank_{idx}.json"
    record_cost(job_id, f"{'judge' if pass_ == 'judge' else 'rank'}:{idx}", resp, config.MAIN_MODEL if pass_ == "judge" else config.SCREEN_MODEL)
    if pass_ == "judge":
        prev = json.loads(path.read_text()) if path.exists() else {}
        try:
            r = extract_json(resp)
        except (ValueError, json.JSONDecodeError):
            prev["judge_failed"] = True  # keep the screening result; never loop on a failing judge call
            path.write_text(json.dumps(prev, indent=1))
            raise
        r["screen"] = {k: prev.get(k) for k in ("quality", "is_highlight", "title", "content_type")}
        r["judged"] = True
    else:
        r = extract_json(resp)
    c = next(x for x in candidates(job_id) if x["idx"] == idx)
    bs = min(max(0.0, float(r["best_start"])), c["duration"])
    be = min(max(bs, float(r["best_end"])), c["duration"])
    meta = db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {}
    length = meta.get("length")
    need = 70 if meta.get("clip_strategy") == "gta_launch_mix" else (length[0] + 10) if length else 12
    if be - bs < need:
        pad = (need - (be - bs)) / 2
        bs, be = max(0.0, bs - pad), min(c["duration"], be + pad)
        if be - bs < need:
            bs, be = 0.0, c["duration"]
    if c.get("story"):
        bs, be = 0.0, c["duration"]
        r["story"] = c["story"]
        abs_start, abs_end = c["start"], c["end"]
    else:
        abs_start, abs_end = c["start"] + bs, c["start"] + be
    r.update(idx=idx, start=round(abs_start, 2), end=round(abs_end, 2),
             duration=round(be - bs, 2), pass1_score=c["pass1_score"], signals=c["signals"],
             quality=max(0.0, min(10.0, float(r["quality"]))), confidence=max(0.0, min(1.0, float(r["confidence"]))))
    (_analysis(job_id) / f"rank_{idx}.json").write_text(json.dumps(r, indent=1))
    return r


def clip_floor(duration: float) -> int:
    """Fewest clips worth delivering from a source of this length: MIN_CLIPS, but never more than one per 90 s.
    (Users who received 1-3 clips came back far less than users who received 4-9, so a single clip is a last resort.)"""
    if duration < 60:
        return 0
    return max(1, min(config.MIN_CLIPS, int(duration // 90) or 1))


def _top_up_to_floor(job_id, chosen: list[dict], ranks: list[dict], iou, explicit: bool) -> list[dict]:
    """Never deliver zero (or too few) clips when usable moments exist: fill up to the floor with the best remaining moments
    that are only a little under the quality bar. They are flagged `below_quality_bar` so the dashboard can say so."""
    if explicit:
        return chosen
    job = db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {}
    dur = float(((job.get("source") or {}).get("duration")) or 0)
    floor = clip_floor(dur)
    if len(chosen) >= floor:
        return chosen
    bar = config.MIN_QUALITY - config.FLOOR_QUALITY_DROP
    rest = sorted((r for r in ranks if r not in chosen and r["quality"] >= bar),
                  key=lambda r: (r["quality"], r["confidence"]), reverse=True)
    added = 0
    for r in rest:
        if len(chosen) >= floor:
            break
        if all(iou(r, o) <= 0.3 for o in chosen):
            r["below_quality_bar"] = True
            chosen.append(r)
            added += 1
    if added:
        db.event(job_id, "info", "select", f"added {added} moment(s) just under the quality bar to reach {floor} clips", {})
    return chosen


def select(job_id) -> list[dict]:
    """Rank, drop weak/overlapping moments, create clip rows. Quality over quantity."""
    ranks = [json.loads(p.read_text()) for p in sorted(_analysis(job_id).glob("rank_*.json"))]
    unranked = [c["idx"] for c in candidates(job_id) if c["idx"] not in {r["idx"] for r in ranks}]
    block = _analysis(job_id) / "budget_block.txt"
    if block.exists() and not ranks:
        msg = block.read_text()
        block.unlink()
        db.set_job(job_id, status="failed", error=msg)
        return []
    if unranked and not ranks:  # Gemini never answered (outage / quota): not the same as "nothing good"
        msg = f"Gemini did not rank any of the {len(unranked)} candidates (API error or outage). Resubmit the video to resume."
        db.set_job(job_id, status="failed", error=msg)
        db.event(job_id, "error", "select", msg, {"unranked": unranked})
        return []
    if unranked:
        db.event(job_id, "warn", "select", f"candidates {unranked} could not be ranked and were skipped", {})
    good = sorted((r for r in ranks if r["is_highlight"] and r["quality"] >= config.MIN_QUALITY),
                  key=lambda r: (r["quality"], r["confidence"]), reverse=True)
    def iou(a, b):
        if a.get("story") or b.get("story"):
            aa = a.get("story", {}).get("beats", [{"start": a["start"], "end": a["end"]}])
            bb = b.get("story", {}).get("beats", [{"start": b["start"], "end": b["end"]}])
            inter = sum(max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"])) for x in aa for y in bb)
            union = sum(x["end"] - x["start"] for x in aa) + sum(y["end"] - y["start"] for y in bb) - inter
            return inter / max(0.001, union)
        inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
        return inter / (max(a["end"], b["end"]) - min(a["start"], b["start"]))
    chosen = []
    for r in good:
        if all(iou(r, o) <= 0.3 for o in chosen):
            chosen.append(r)
    want = (db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {}).get("clips")
    if want:  # the user asked for an exact number: trim, or top up with the best remaining moments
        chosen = chosen[:want]
        rest = sorted((r for r in ranks if r not in chosen), key=lambda r: (r["quality"], r["confidence"]), reverse=True)
        for r in rest:
            if len(chosen) >= want:
                break
            if all(iou(r, o) <= 0.3 for o in chosen):
                r["below_quality_bar"] = True
                chosen.append(r)
    chosen = _top_up_to_floor(job_id, chosen, ranks, iou, bool(want))
    from . import budget
    left = min(budget.remaining(job_id).values())
    fit = max(0, int(left // budget.PER_CLIP))
    if len(chosen) > fit:  # never start edits the spending limits cannot pay for
        db.event(job_id, "warn", "select", f"spending limits allow {fit} of {len(chosen)} clips (left ${left:.3f})", {})
        if fit == 0:
            msg = budget.blocked(job_id, budget.PER_CLIP) or "Spending limit reached."
            db.set_job(job_id, status="failed", error=msg)
            return []
        chosen = chosen[:fit]
    meta = db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {}
    if meta.get("clip_strategy") == "gta_launch_mix" and chosen:
        def available(r):
            beats = (r.get("story") or {}).get("beats") or []
            return sum(float(b["end"]) - float(b["start"]) for b in beats) if beats else float(r.get("duration") or 0)
        capable = sorted((r for r in chosen if available(r) >= 58),
                         key=lambda r: (bool(r.get("story")), available(r), r["quality"]), reverse=True)
        long_ids = {r["idx"] for r in capable[:max(1, len(chosen) // 2)]}
        for r in chosen:
            is_story = r["idx"] in long_ids
            is_extended = is_story and available(r) >= 90 and bool(r.get("story"))
            target = [80, 120] if is_extended else [60, 80] if is_story else [20, 35]
            r["opts"] = {**(r.get("opts") or {}), "length": target}
            r["version_type"] = "extended_story" if is_extended else "story" if is_story else "viral_short"
        if not long_ids:
            db.event(job_id, "warn", "select", "launch test found no moment with enough real story for a 60-second cut; kept the batch short instead of padding weak footage", {})
    for r in chosen:
        db.q("insert into clips(job_id, idx, candidate) values (%s,%s,%s) on conflict (job_id, idx) do nothing",
             (job_id, r["idx"], db.J(r)))
    db.set_job(job_id, status="selected" if chosen else "no_highlights")
    db.event(job_id, "info", "select", f"{len(chosen)} of {len(ranks)} candidates selected",
             {"rejected": [{"idx": r["idx"], "quality": r["quality"], "is_highlight": r["is_highlight"]}
                           for r in ranks if r not in chosen]})
    return chosen


def _silences(tr, s, e, min_gap=0.8):
    ws = [w for w in tr["words"] if w["e"] > s and w["s"] < e]
    gaps, prev = [], s
    for w in ws:
        if w["s"] - prev >= min_gap:
            gaps.append(f"{prev:.2f}-{w['s']:.2f}")
        prev = w["e"]
    if e - prev >= min_gap:
        gaps.append(f"{prev:.2f}-{e:.2f}")
    return ", ".join(gaps) or "none"


def _chat_peaks(a: Path, start: float, end: float) -> list[int]:
    p = a / "chat.json"
    if not p.exists():
        return []
    ch = json.loads(p.read_text())
    if not ch:
        return []
    from .signals import peaks
    return [t for t in peaks(ch["hype"], 2.5) if start <= t <= end][:5]


def clip_meta(job_id, idx: int) -> dict:
    """The job's options with any per-clip override on top (a clip regenerated in another mode keeps that choice)."""
    jm = db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {}
    row = db.q1("select candidate from clips where job_id=%s and idx=%s", (job_id, idx)) or {}
    return {**jm, **((row.get("candidate") or {}).get("opts") or {})}


def director_request(job_id, idx: int) -> dict:
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    c = clip["candidate"]
    a = _analysis(job_id)
    from .analysis import refine_transcript
    from . import preguide
    ranges = preguide.story_ranges(job_id, c) or [(c["start"], c["end"])]  # story parts, lengthened if too short
    tr = None
    for rs, re_ in ranges:
        tr = refine_transcript(job_id, rs, re_)
    face = clip_facecam(job_id, idx, c)
    words = " ".join(f"[{w['s']:.2f}]{w['w']}" for w in tr["words"] if any(w["e"] > s and w["s"] < e for s, e in ranges)) or "(no speech)"
    # director sees a proxy cut exactly to the selected window
    from .analysis import make_proxy, make_story_proxy
    job = db.q1("select source_path from jobs where id=%s", (job_id,))
    proxy = a / f"clip_{idx}_{c['start']:.2f}_{c['end']:.2f}_{sum(e - s0 for s0, e in ranges):.0f}.mp4"
    if c.get("story"):
        make_story_proxy(Path(job["source_path"]), proxy, [{**b, "start": r[0], "end": r[1]} for b, r in zip(c["story"]["beats"], ranges)])
    else:
        make_proxy(Path(job["source_path"]), proxy, c["start"], c["end"])
    last = (a / f"edl_{idx}_last_error.json")
    repair = ""
    if last.exists():
        le = json.loads(last.read_text())
        repair = ("\n## Your previous attempt was rejected. Fix these problems and return the full corrected JSON:\n- "
                  + "\n- ".join(le["errors"][:12]) + "\nPrevious output:\n" + le["raw"][:6000])
    gbox = c.get("facecam_box") if c.get("has_facecam") else None
    if not face.get("found") and gbox and all(k in gbox for k in "xywh") and 0.03 < gbox["w"] < 0.9 and 0.03 < gbox["h"] < 0.9:
        b = {k: round(max(0.0, min(1.0, float(gbox[k]))), 3) for k in "xywh"}
        b["w"], b["h"] = min(b["w"], 1 - b["x"]), min(b["h"], 1 - b["y"])
        face = {"found": True, "box": b, "hit_rate": 1.0, "source": "visual review"}
    fc = (f"Facecam found at box {json.dumps(face['box'])} (normalised x,y,w,h), seen in {face['hit_rate']:.0%} of sampled frames."
          + (" The face fills much of the frame (camera-only video): prefer layout 'full' or 'fit'." if face.get("fullscreen_face") else "")
          ) if face.get("found") else "No stable facecam detected. Use layout 'fit' or 'full' unless you clearly see a webcam box."
    if face.get("found") and c.get("reaction_peaks"):
        fc += (" The streamer visibly reacts on camera (big movement) at source time(s) "
               + ", ".join(f"{t}s" for t in c["reaction_peaks"][:6]) + ": strong spots for a facecam zoom.")
    chat_peaks = _chat_peaks(a, c["start"], c["end"])
    if chat_peaks:
        fc += " Live chat exploded (shifted to the on-screen moment) at source time(s) " + ", ".join(f"{t}s" for t in chat_peaks) + "."
    from .styles import MOMENT_STRATEGY, STYLES, style_of
    job_meta = clip_meta(job_id, idx)
    style = style_of(clip.get("style") or job_meta.get("style"))
    st = STYLES[style]
    ctype = (job_meta.get("content_type") if job_meta.get("content_type") not in (None, "auto") else None) or c.get("content_type") or "gameplay"
    fb = a / f"review_{idx}.json"
    feedback = ""
    if fb.exists():
        rv = json.loads(fb.read_text())
        if rv.get("redo_requested") and not rv.get("rounds", [None])[-1].get("after_redo"):
            last_r = rv["rounds"][-1]
            feedback = ("\n## A reviewer watched your previous rendered edit and scored it "
                        f"{last_r['overall']:.1f}/10. Make a clearly better edit that fixes these points:\n- "
                        + "\n- ".join((last_r.get("fixes") or last_r.get("issues") or [])[:8]) + "\n")
    length = job_meta.get("length")
    length_rule = (f"{length[0]}-{length[1]} seconds (sum of ALL segments, including the cold_open). Hitting this range is required."
                   if length else "YOU decide per clip, anything from 20 to 90 seconds: as long as this story needs to be complete "
                   "(setup, build-up, payoff, reaction) and no longer. Quick punchline: 20-30 s. Normal moment: 40-60 s. "
                   "A vlog/IRL story with a real build-up: 60-90 s. Do not default to short clips, and cut dead air instead "
                   "of story.")
    bc = job_meta.get("burned_captions") or {}
    burned = ""
    if bc.get("present"):
        caption_position = "middle" if bc.get("where") == "bottom" else "lower"
        burned = (f"## The source already has captions burned into the picture\n"
                  f"Subtitles are visible in the {bc.get('where')} part of the source frame ({int(bc.get('coverage', 0) * 100)}% of sampled frames), "
                  f"e.g. {', '.join(repr(x) for x in (bc.get('examples') or [])[:3])}. Do NOT stack our captions on top of them: "
                  "set captions.enabled=false, unless the source captions are sparse and ours add real value; then set "
                  f"captions.position to '{caption_position}' so the two never overlap.\n\n")
        burned = burned.replace("captions.position to {'\\'middle\\'' if bc.get('where') == 'bottom' else '\\'lower\\''}", f"captions.position to '{caption_position}'")
    from .sfxlib import prompt_list
    from .trends import prompt_section
    story_context = ""
    if c.get("story"):
        story_context = ("\n## Connected story plan (mandatory)\nStory: " + c["story"].get("summary", "") +
                         "\nWhy the timestamps connect: " + c["story"].get("connection", "") +
                         "\nYou may use footage ONLY inside these source ranges: " + json.dumps(ranges) +
                         "\nPreserve their narrative roles and do not add footage from the gaps between them.\n")
    try:  # the edit guide's research, run before editing: where the streamer's webcam is in each part
        from . import preguide
        pre = preguide.prompt_section(preguide.research(job_id, idx, c))
        preguide.mark_sent(job_id, idx)
    except Exception as e:  # noqa: BLE001 - the plan can still be made without it
        pre = ""
        db.event(job_id, "warn", "director", f"clip {idx}: pre-edit webcam check failed: {str(e)[:150]}", {})
    from . import hookrules
    has_voice = len(tr["words"]) >= 8 and any(w["e"] > s and w["s"] < e for s, e in ranges for w in tr["words"][:4000])
    from .templates import prompt_rule
    pre += prompt_rule(job_meta.get("template"))
    from . import modes
    pre += modes.director_rule(job_meta)
    from .manage import load_settings
    pre += ("\n## Owner's house rules\n"
            + ("- NO music: set music.enabled=false.\n" if not load_settings().music_enabled else "")
            + "- captions.emphasis: put the one or two strongest words FIRST (they are shown in red); the rest are shown in "
              "neon green. Use few: 3-6 words.\n"
              "- Sound effects: set `at` on the exact action (the hit, the jump, the line that lands, the text popping in). "
              "The code moves each sound so its loudest instant lands on that action, so impacts hit with it and whooshes "
              "build up just before it. Fewer, well-placed sounds beat many.\n")
    prompt = DIRECTOR_PROMPT.format(hook_rules=hookrules.prompt_rules(),
                                    playbook=hookrules.playbook_text(c.get("content_type"), None, has_voice),
                                    trends=prompt_section(job_meta.get("trends")), sfx_list=prompt_list(), burned=burned + story_context + pre, length_rule=length_rule, content_type=ctype, content_guide=CONTENT_GUIDE.get(ctype, ""), feedback=feedback, style=style, style_label=st["label"], style_guide=st["guide"],
                                    style_limits=json.dumps(modes.effective_limits(job_meta, st["limits"])), moment_strategy=MOMENT_STRATEGY,
                                    event=c["event"], why=c["why_engaging"], hook=c["hook"], start=c["start"], end=c["end"],
                                    words=words, silences=", ".join(_silences(tr, s, e) for s, e in ranges), facecam=fc,
                                    schema=json.dumps(EDL.model_json_schema(), separators=(",", ":")), repair=repair)
    return {"idx": idx, "model": config.MAIN_MODEL, "body": _body(proxy, prompt, None, 0.5, fps=2, low_res=True, max_out=24576)}


def trim_dead_opening(job_id, edl: EDL, src: str | None = None) -> tuple[EDL, float]:
    """Move the first segment's start past black / faded-in frames (the first second decides the swipe)."""
    import subprocess
    src = src or db.q1("select source_path from jobs where id=%s", (job_id,))["source_path"]
    s0 = edl.segments[0]
    look = min(4.0, s0.end - s0.start - 1.0)
    if look < 0.3:
        return edl, 0.0
    p = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{s0.start}", "-t", f"{look}", "-i", src, "-vf",
                        "scale=320:-2,blackdetect=d=0.1:pic_th=0.90:pix_th=0.12", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=60)
    m = re.search(r"black_start:([\d.]+)\s+black_end:([\d.]+)", p.stderr)
    if not m or float(m.group(1)) > 0.1:
        return edl, 0.0
    cut = round(float(m.group(2)) + 0.05, 2)
    if cut >= look:
        return edl, 0.0
    segs = [s0.model_copy(update={"start": round(s0.start + cut, 3)})] + list(edl.segments[1:])
    return edl.model_copy(update={"segments": segs}), cut


def _snap(job_id, edl: EDL, c: dict) -> tuple[EDL, list[str], str | None]:
    """Move every cut onto a word gap / sentence end / scene change (code-enforced; AI timestamps are approximate).
    Returns an error text when the clip would still END mid-sentence, so the director repairs it."""
    from . import timing
    a = _analysis(job_id)
    tr = json.loads((a / "transcript.json").read_text())
    cuts = json.loads((a / "scene_cuts.json").read_text()) if (a / "scene_cuts.json").exists() else []
    cuts = cuts if isinstance(cuts, list) else cuts.get("cuts", [])
    segs = [s.model_dump() for s in edl.segments]
    lo, hi = min(s["start"] for s in segs) - 2, max(s["end"] for s in segs) + 2
    if config.CAPTION_ALIGN and not c.get("story"):  # story beats were separately refined; never align the hours between them
        from .align import align_words
        words = align_words(tr["words"], a / "audio.wav", lo, hi, tr.get("language"), cache=a / "aligned.json")
    else:
        words = [w for w in tr["words"] if w["e"] > lo and w["s"] < hi]
    snapped, notes = timing.snap_segments(segs, words, cuts, (c["start"], c["end"]))
    if not snapped:
        return edl, notes, None
    old_end = max(s["end"] for s in snapped if s.get("role", "main") == "main")
    ext = timing.extend_tail(snapped, words, c["end"])
    new_end = max(s["end"] for s in ext if s.get("role", "main") == "main")
    if new_end - old_end >= 0.15:
        notes.append(f"ending kept {new_end - old_end:.1f}s longer so the payoff lands")
        snapped = ext
    edl = edl.model_copy(update={"segments": [type(edl.segments[0]).model_validate(s) for s in snapped]})
    last_main = [s for s in snapped if s.get("role", "main") == "main"][-1]
    if timing.ends_mid_sentence(last_main["end"], words):  # finish the sentence (or end at the previous one) instead of failing
        more = [w for w in tr["words"] if last_main["end"] - 0.02 <= w["s"] <= last_main["end"] + 8.0]
        fin = next((w for w in more if timing.SENT_END.search(w["w"])), None)
        prev = [w for w in words if w["e"] <= last_main["end"] and w["s"] > last_main["start"] + 2.0 and timing.SENT_END.search(w["w"])]
        def _room(w):  # a beat after the line (QC wants >= 0.35 s, ideally 0.5), never into the next word
            nxt = next((x["s"] for x in tr["words"] if x["s"] > w["e"] + 0.01), None)
            return max(0.0, min(0.6, (nxt - 0.08 - w["e"]) if nxt is not None else 0.6))
        if fin:
            last_main["end"] = round(fin["e"] + _room(fin), 3)
            notes.append(f"ending moved to {last_main['end']:.2f}s so the sentence finishes")
        elif prev:
            last_main["end"] = round(prev[-1]["e"] + _room(prev[-1]), 3)
            notes.append(f"ending moved back to {last_main['end']:.2f}s, the end of the previous sentence")
        edl = edl.model_copy(update={"segments": [type(edl.segments[0]).model_validate(s) for s in snapped]})
    # nonstop speech has no pause to snap to: never leave a cut INSIDE a word, cut on the word's edge instead
    moved = False
    for seg in snapped:
        for w in words:
            if w["s"] + 0.02 < seg["start"] < w["e"] - 0.02:  # same tolerance as QC (timing._inside_word)
                seg["start"], moved = round(w["s"], 3), True
            if w["s"] + 0.02 < seg["end"] < w["e"] - 0.02:
                seg["end"], moved = round(w["e"], 3), True
    if moved:
        notes.append("cuts moved to word edges (no pause nearby)")
        edl = edl.model_copy(update={"segments": [type(edl.segments[0]).model_validate(s) for s in snapped]})
    mid = None
    if timing.ends_mid_sentence(last_main["end"], words):
        mid = (f"the clip ends mid-sentence at {last_main['end']:.2f}s; end the last segment where the sentence ends "
               "(or before it starts) so the clip ends on a complete line")
    return edl, notes, mid


def _cut_dead_air(job_id, edl: EDL, length, idx: int | None = None) -> tuple[EDL, list[str]]:
    """Remove stretches where nobody talks and nothing happens (Settings: dead_air_cut)."""
    from .deadair import cut_dead_air
    from .manage import load_settings
    from . import modes
    meta = clip_meta(job_id, idx) if idx is not None else (db.q1("select meta from jobs where id=%s", (job_id,))["meta"] or {})
    gap = modes.silence_gap(meta)  # None: owner turned it off; False: not chosen, use Settings
    if gap is None or (gap is False and not load_settings().dead_air_cut):
        return edl, []
    a = _analysis(job_id)
    try:
        tr = json.loads((a / "transcript.json").read_text())
        feats = json.loads((a / "audio_features.json").read_text())
        fm = json.loads((a / "face_motion.json").read_text()) if (a / "face_motion.json").exists() else None
    except (OSError, ValueError):
        return edl, []
    from .signals import peaks
    reactions = [float(t) for t in peaks(fm, 3.0)] if fm else []
    segs = [s.model_dump() for s in edl.segments]
    min_total = float(length[0]) if length else 12.0
    frozen = []
    try:  # loading screens / menus: a frozen picture with no talking is cut after a shorter pause
        from .frozen import frozen_intervals
        src = db.q1("select source_path from jobs where id=%s", (job_id,))["source_path"]
        for s in edl.segments:
            if s.role == "main" and s.end - s.start <= 400:
                frozen += frozen_intervals(src, s.start, s.end)
    except Exception as e:  # noqa: BLE001 - silence cutting still works without it
        db.event(job_id, "warn", "director", f"frozen-picture scan failed: {str(e)[:120]}", {})
    new, notes = cut_dead_air(segs, tr["words"], feats["rms_db"], feats["hop"], reactions, min_total, gap or 2.5, frozen)
    if not notes:
        return edl, []
    seg_cls = type(edl.segments[0])
    return edl.model_copy(update={"segments": [seg_cls.model_validate(x) for x in new]}), notes


def clip_facecam(job_id, idx: int, c: dict) -> dict:
    """Webcam position for THIS clip's time window: stream layouts change (another streamer's cam, a full-screen
    'just chatting' scene), so the box found for the whole video can point at the wrong person later on."""
    a = _analysis(job_id)
    f = a / f"facecam_clip_{idx}.json"
    if f.exists():
        return json.loads(f.read_text())
    whole = json.loads((a / "facecam.json").read_text())
    try:
        from .signals import faces
        src = Path(db.q1("select source_path from jobs where id=%s", (job_id,))["source_path"])
        hint = c.get("facecam_box") if c.get("has_facecam") else None  # where Gemini saw the streamer's own cam
        face = faces(src, max(2.0, c["end"] - c["start"]), 12, start=c["start"], hint=hint)
        face["scope"] = "clip"
        fb = face.get("face") or {}
        if (face.get("found") and hint and (face.get("box") or {}).get("method") == "guess" and fb
                and hint["x"] <= fb["x"] + fb["w"] / 2 <= hint["x"] + hint["w"] and hint["y"] <= fb["y"] + fb["h"] / 2 <= hint["y"] + hint["h"]
                and 0.05 < hint["w"] < 0.6 and 0.05 < hint["h"] < 0.6):
            # no clear webcam border found: Gemini's rectangle around the same face frames the whole webcam
            face["box"] = {**{k: round(float(hint[k]), 3) for k in "xywh"}, "method": "gemini"}
    except Exception as e:  # noqa: BLE001 - fall back to the whole-video detection
        face = {**whole, "scope": "video", "clip_error": str(e)[:150]}
    f.write_text(json.dumps(face))
    if whole.get("found") and face.get("found") and abs(whole["box"]["x"] - face["box"]["x"]) + abs(whole["box"]["y"] - face["box"]["y"]) > 0.1:
        db.event(job_id, "info", "director", f"clip {idx}: the webcam is in a different place here than in most of the video", {"clip": face["box"], "video": whole["box"]})
    return face


def save_edl(job_id, idx: int, resp: dict) -> dict:
    record_cost(job_id, f"director:{idx}", resp, config.MAIN_MODEL)
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    c = clip["candidate"]
    from .preguide import story_ranges
    rs = story_ranges(job_id, c)
    if rs:  # story parts lengthened by the pre-edit guide: the clip window grows with them, plus snap slack so a cut
        # at a range edge that falls inside a word can still move to the nearest word gap
        from .edl import SNAP_SLACK
        c = {**c, "start": max(0.0, min(c["start"], min(r[0] for r in rs)) - SNAP_SLACK),
             "end": max(c["end"], max(r[1] for r in rs)) + SNAP_SLACK}
    last = _analysis(job_id) / f"edl_{idx}_last_error.json"
    raw, errors, warnings, edl = "", [], [], None
    try:
        data = extract_json(resp)
        raw = json.dumps(data)
        edl = EDL.model_validate(data)
        from .edl import fit_story_ranges
        edl, fit_notes = fit_story_ranges(edl, rs)  # trim into the story ranges first, then snap cuts to word gaps
        edl, snap_notes, mid = _snap(job_id, edl, c)
        snap_notes = fit_notes + snap_notes
        c = {**c, "end": max(c["end"], max(s.end for s in edl.segments))}  # a finished sentence may run past the window
        jmeta = clip_meta(job_id, idx)
        length = jmeta.get("length")
        edl, dead_notes = _cut_dead_air(job_id, edl, length, idx)
        snap_notes = snap_notes + dead_notes
        from .preguide import story_ranges
        allowed = story_ranges(job_id, c)  # the story parts (lengthened by the pre-edit guide when too short)
        prior = json.loads(last.read_text()).get("attempts", 0) if last.exists() else 0
        game = jmeta.get("game")
        # hook/title rule breaks send the plan back for the first two attempts; the third keeps the clip and warns
        errors, warnings, edl = check(edl, c["start"], c["end"], tuple(length) if length else None, allowed_ranges=allowed,
                                      strict_hook=prior < 2, game=game, job_meta=jmeta)
        if edl and edl.layout.mode == "fit" and (c.get("content_type") in ("cinematic", "gameplay", "montage")) and not c.get("has_facecam"):
            edl.layout.mode = "full"  # empty bars look bad on these sources: fill the screen
            warnings.append("layout 'fit' changed to 'full' (full-screen crop for this content type)")
        if edl:  # the layout template chosen for the job (auto = the AI's choice)
            from .templates import apply_template
            tmpl = jmeta.get("template")
            edl, lay_note = apply_template(job_id, edl, c, tmpl, jmeta)
            if lay_note:
                warnings.append(lay_note)
        if edl:  # the owner's mode: continuous keeps the original order, captions follow the chosen look
            if jmeta.get("mode") == "continuous":
                keep = [s for s in edl.segments if s.role != "cold_open"]
                if keep and len(keep) < len(edl.segments):
                    edl = edl.model_copy(update={"segments": keep})
                    warnings.append("cold open removed (continuous mode keeps the original order)")
            look = jmeta.get("caption_look")
            if look in ("pop", "karaoke", "clean"):
                edl.captions.style = "pop" if look == "pop" else "karaoke"
                if look == "clean":
                    edl.captions.position = "lower"
        warnings = snap_notes + warnings
        if mid:  # repaired on the first two attempts; the last one keeps the clip with a warning instead of failing it
            from . import budget as _budget
            last_try = prior >= 2 or bool(_budget.blocked(job_id, _budget.EST["director"] * 2))  # no retry is coming
            (warnings if last_try else errors).append(mid)
    except ValidationError as e:
        errors = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
    except (ValueError, json.JSONDecodeError) as e:
        cut = ((resp.get("candidates") or [{}])[0].get("finishReason") == "MAX_TOKENS") if isinstance(resp, dict) else False
        errors = [f"not valid JSON: {e}" + (" (your answer was cut off: it ran out of length; keep reasons short)" if cut else "")]
    if errors:
        attempts = (json.loads(last.read_text()).get("attempts", 0) if last.exists() else 0) + 1
        last.write_text(json.dumps({"errors": errors, "raw": raw, "attempts": attempts}))
        db.event(job_id, "warn", "director", f"EDL rejected for clip {idx} (attempt {attempts})", {"errors": errors[:8]})
        from . import budget
        over = attempts < 3 and budget.blocked(job_id, budget.EST["director"] * 2)  # a retry must fit the spending limits
        if over:
            db.event(job_id, "warn", "director", f"clip {idx}: no more plan retries ({over})", {})
            attempts = 3
        if attempts >= 3:
            rf = _review_file(job_id, idx)
            rv = json.loads(rf.read_text()) if rf.exists() else {}
            if rv.get("redo_requested") and rv.get("prev"):  # an AI-review redo failed: keep the first good version
                import shutil
                shutil.copy2(rv["prev"]["file"], clip["output_path"])
                db.q("update clips set edl=%s, qc=%s, status='ready_for_review' where job_id=%s and idx=%s",
                     (db.J(rv["prev"]["edl"]), db.J(rv["prev"]["qc"]), job_id, idx))
                rv["rounds"].append({"overall": rv["rounds"][-1]["overall"], "after_redo": True, "note": "redo plan rejected"})
                rf.write_text(json.dumps(rv, indent=1))
                last.unlink(missing_ok=True)
                db.event(job_id, "warn", "review", f"clip {idx}: redo plan rejected 3 times; kept the first version", {})
                return {"valid": False, "errors": errors, "warnings": warnings, "attempts": attempts, "retry": False}
            db.q("update clips set status='failed' where job_id=%s and idx=%s", (job_id, idx))
        return {"valid": False, "errors": errors, "warnings": warnings, "attempts": attempts, "retry": attempts < 3}
    last.unlink(missing_ok=True)
    edl, trimmed = trim_dead_opening(job_id, edl) if edl.segments[0].role == "main" else (edl, 0.0)
    if trimmed:
        _, more_warn, edl = check(edl, c["start"], c["end"], tuple(length) if length else None, job_meta=jmeta)
        warnings += [f"opening trimmed by {trimmed:.2f}s (black/fade frames)"] + more_warn
    db.q("update clips set edl=%s, status='planned', qc=null, review='pending' where job_id=%s and idx=%s", (db.J(edl.model_dump()), job_id, idx))
    from .preguide import mark_plan
    mark_plan(job_id, idx)  # remember that this plan was made with the pre-edit guide
    # new plan => previous render/qc results are stale (render cache is keyed by EDL content)
    db.q("delete from stages where job_id=%s and clip_idx=%s and name in ('render','qc') and status in ('done','failed')", (job_id, idx))
    return {"valid": True, "errors": [], "warnings": warnings, "retry": False, "edl": edl.model_dump()}


REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hook": {"type": "NUMBER"}, "clarity": {"type": "NUMBER"}, "pacing": {"type": "NUMBER"},
        "captions": {"type": "NUMBER"}, "framing": {"type": "NUMBER"}, "effects": {"type": "NUMBER"},
        "payoff": {"type": "NUMBER"}, "overall": {"type": "NUMBER"},
        "issues": {"type": "ARRAY", "items": {"type": "STRING"}},
        "fixes": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["hook", "clarity", "pacing", "captions", "framing", "effects", "payoff", "overall", "issues", "fixes"],
}

REVIEW_PROMPT = """You are a strict short-form video reviewer. Watch and LISTEN to this finished vertical clip ({dur:.0f}s),
made from {ctype} footage in the "{style}" editing style. Intended hook: "{hook}".
Score each 0-10 (5 = mediocre, 8 = good, 10 = great), judging it as a TikTok/Shorts post for viewers with no context:
- hook: do the first 2 seconds stop the scroll? (black/empty opening = low)
- clarity: is it understandable on its own?
- pacing: no dead air, no rushed or abrupt cuts, no cut mid-word
- captions: readable, in sync, correct words, not covering the key action
- framing: the important action is visible, not cropped away, not over-zoomed or blurry
- effects: zooms/SFX/emoji have a purpose; nothing distracting or cheap-looking
- payoff: does it end on the payoff instead of fading out or cutting early?
- overall: your honest overall score (not an average).
issues: concrete problems with the second they happen ("0:03 caption covers the car").
fixes: concrete instructions for the editor to fix them ("start at the line 'Hey, hands above'").
Return JSON only."""


def _final_proxy(src: Path, dst: Path):
    from . import media
    if dst.exists():
        return
    tmp = dst.with_suffix(".tmp.mp4")
    media.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *media.hwdec(), "-i", str(src), "-vf", "scale=-2:854,fps=10",
               *media.h264("review"), "-ac", "1", "-ar", "16000", "-b:a", "48k",
               "-movflags", "+faststart", str(tmp)], timeout=600)
    tmp.rename(dst)


def _review_file(job_id, idx) -> Path:
    return _analysis(job_id) / f"review_{idx}.json"


def review_request(job_id, idx: int) -> dict:
    """Gemini watches the finished MP4. Skipped when the clip failed QC or was already reviewed after a redo."""
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not clip or clip["status"] != "ready_for_review" or not clip["output_path"]:
        return {"skip": True, "reason": f"status {clip and clip['status']}"}
    rf = _review_file(job_id, idx)
    rv = json.loads(rf.read_text()) if rf.exists() else {"rounds": []}
    edl_key = json.dumps(clip["edl"], sort_keys=True)
    if any(r.get("edl_key") == edl_key for r in rv["rounds"]) or len(rv["rounds"]) > config.REVIEW_REDOS:
        return {"skip": True, "reason": "already reviewed"}
    proxy = _analysis(job_id) / f"final_{idx}_{len(rv['rounds'])}.mp4"
    _final_proxy(Path(clip["output_path"]), proxy)
    from . import media
    edl = clip["edl"]
    prompt = REVIEW_PROMPT.format(dur=media.probe(proxy)["duration"], ctype=clip["candidate"].get("content_type", "gameplay"),
                                  style=edl.get("style"), hook=(edl.get("hook") or {}).get("text", ""))
    from . import budget
    if budget.blocked(job_id, budget.EST["review"]):
        return {"skip": True, "reason": "spending limit"}
    return {"idx": idx, "model": config.MAIN_MODEL, "body": _body(proxy, prompt, REVIEW_SCHEMA, 0.2)}


def save_review(job_id, idx: int, resp: dict) -> dict:
    """Store the score; ask for one redo below REVIEW_MIN; after a redo keep whichever version scored higher."""
    import shutil
    record_cost(job_id, f"review:{idx}", resp, config.MAIN_MODEL)
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    rf = _review_file(job_id, idx)
    rv = json.loads(rf.read_text()) if rf.exists() else {"rounds": []}
    try:
        r = extract_json(resp)
        r["overall"] = max(0.0, min(10.0, float(r["overall"])))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        db.event(job_id, "warn", "review", f"clip {idx}: AI review unavailable ({str(e)[:120]}); keeping the clip", {})
        return {"redo": False, "reviewed": False}
    after_redo = bool(rv.get("redo_requested"))
    r.update(edl_key=json.dumps(clip["edl"], sort_keys=True), after_redo=after_redo)
    rv["rounds"].append(r)
    redo, verdict = False, "keep"
    from . import budget
    if not after_redo and r["overall"] < config.REVIEW_MIN and config.REVIEW_REDOS > 0 and not budget.blocked(job_id, budget.PER_CLIP):
        backup = _analysis(job_id) / f"review_{idx}_prev.mp4"
        shutil.copy2(clip["output_path"], backup)
        rv.update(redo_requested=True, prev={"edl": clip["edl"], "qc": clip["qc"], "file": str(backup)})
        redo, verdict = True, "redo"
    elif after_redo:
        first = rv["rounds"][-2]["overall"] if len(rv["rounds"]) > 1 else 0
        if r["overall"] < first and rv.get("prev"):  # the redo was worse: put the first version back
            shutil.copy2(rv["prev"]["file"], clip["output_path"])
            db.q("update clips set edl=%s, qc=%s where job_id=%s and idx=%s",
                 (db.J(rv["prev"]["edl"]), db.J(rv["prev"]["qc"]), job_id, idx))
            verdict = "reverted to first version"
        else:
            verdict = "redo kept"
    rv["final_score"] = max(x["overall"] for x in rv["rounds"])
    rf.write_text(json.dumps(rv, indent=1))
    db.q("update clips set ai_review=%s where job_id=%s and idx=%s",
         (db.J({"score": rv["final_score"], "rounds": [{k: x.get(k) for k in ("overall", "hook", "clarity", "pacing", "captions",
                "framing", "effects", "payoff", "issues")} for x in rv["rounds"]], "verdict": verdict}), job_id, idx))
    db.event(job_id, "info", "review", f"clip {idx}: AI review {r['overall']:.1f}/10 -> {verdict}", {"issues": r.get("issues", [])[:5]})
    return {"redo": redo, "reviewed": True, "score": r["overall"], "verdict": verdict}


MODERATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "sexual": {"type": "STRING", "enum": ["none", "low", "medium", "high"]},
        "gore": {"type": "STRING", "enum": ["none", "low", "medium", "high"]},
        "hate": {"type": "STRING", "enum": ["none", "low", "medium", "high"]},
        "self_harm": {"type": "STRING", "enum": ["none", "low", "medium", "high"]},
        "drugs": {"type": "STRING", "enum": ["none", "low", "medium", "high"]},
        "issues": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["sexual", "gore", "hate", "self_harm", "drugs", "issues"],
}

MODERATION_PROMPT = """You check a finished short gaming clip ({dur:.0f}s) before it is posted on YouTube Shorts, TikTok and Instagram Reels.
Watch and LISTEN. Rate each category none/low/medium/high for what is actually shown or said:
- sexual: nudity or sexual content (game or real people)
- gore: graphic, realistic blood/mutilation. Ordinary video-game action (shooting, fights, crashes, cartoonish blood) is "low" at most.
- hate: slurs or hateful content against a protected group (spoken, written or shown)
- self_harm: depiction or encouragement of self-harm or suicide
- drugs: real drug use or promotion (in-game references without glorification are "low")
issues: each concrete problem with its second ("0:12 slur in the voice chat"). Empty if none.
Judge it like the platforms' community guidelines would. Normal mature-game content (GTA-style violence, swearing) is acceptable.
Return JSON only."""

FLAG_AT = {"sexual": "medium", "gore": "high", "hate": "medium", "self_harm": "medium", "drugs": "high"}
_LEVEL = {"none": 0, "low": 1, "medium": 2, "high": 3}


def moderation_flags(r: dict) -> list[str]:
    return [f"{k} ({r.get(k)})" for k, at in FLAG_AT.items() if _LEVEL.get(str(r.get(k)), 0) >= _LEVEL[at]]


def moderation_request(job_id, idx: int) -> dict:
    """Optional NSFW check of the finished MP4 (off by default; Settings -> Moderation)."""
    from . import budget, media
    from .manage import load_settings
    if not load_settings().moderation_enabled:
        return {"skip": True, "reason": "moderation is off in Settings"}
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    if not clip or clip["status"] != "ready_for_review" or not clip["output_path"]:
        return {"skip": True, "reason": f"status {clip and clip['status']}"}
    if (clip["qc"] or {}).get("moderation", {}).get("edl_key") == json.dumps(clip["edl"], sort_keys=True):
        return {"skip": True, "reason": "already checked"}
    if budget.blocked(job_id, budget.EST["moderation"]):
        return {"skip": True, "reason": "spending limit"}
    proxy = _analysis(job_id) / f"moderate_{idx}.mp4"
    proxy.unlink(missing_ok=True)
    _final_proxy(Path(clip["output_path"]), proxy)
    prompt = MODERATION_PROMPT.format(dur=media.probe(proxy)["duration"])
    return {"idx": idx, "model": config.SCREEN_MODEL, "body": _body(proxy, prompt, MODERATION_SCHEMA, 0.0)}


def save_moderation(job_id, idx: int, resp: dict) -> dict:
    """Store the result in clip.qc; a flagged clip fails QC so it never reaches review or posting."""
    record_cost(job_id, f"moderation:{idx}", resp, config.SCREEN_MODEL)
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    try:
        r = extract_json(resp)
    except (ValueError, json.JSONDecodeError) as e:
        db.event(job_id, "warn", "moderation", f"clip {idx}: moderation unavailable ({str(e)[:120]}); clip not checked", {})
        return {"checked": False}
    flags = moderation_flags(r)
    qc = dict(clip["qc"] or {})
    qc["moderation"] = {**r, "flags": flags, "edl_key": json.dumps(clip["edl"], sort_keys=True)}
    status = clip["status"]
    if flags:
        qc["passed"] = False
        qc["fails"] = list(qc.get("fails") or []) + ["moderation: " + ", ".join(flags)]
        status = "qc_failed"
    db.q("update clips set qc=%s, status=%s where job_id=%s and idx=%s", (db.J(qc), status, job_id, idx))
    db.event(job_id, "warn" if flags else "info", "moderation", f"clip {idx}: " + ("flagged: " + ", ".join(flags) if flags else "ok"), {"issues": r.get("issues", [])[:5]})
    return {"checked": True, "flagged": bool(flags), "flags": flags}
