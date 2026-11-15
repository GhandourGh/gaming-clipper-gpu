"""Pre-edit guide: the Tools page's edit guide, built automatically BEFORE the AI editor plans a clip, so the AI edits
from a researched brief instead of starting cold. For the chosen moment (each part of a multi-part story, or the clip
window) it prepares, from the local analysis only ($0: Apple Vision, ffmpeg, the existing transcript and signals):
  - the parts in story order with their role, reason and quote, and time-jump labels ("2 HOURS LATER" / "EARLIER")
    where the story jumps back and forth in the stream;
  - a cold-open teaser (the payoff's peak), zooms (on-camera reactions -> webcam, loud action -> gameplay),
    sound effects (screams, big hits), on-screen text callouts (the loudest short lines), caption words to highlight,
    dead air to cut, hook and title options, music mood.
The director prompt gets it as its brief (it may deviate when the video shows better, with a reason), and the Tools page
shows the guide next to what the AI did. (Webcam detection/tracking per part was removed on the owner's request.)
Cached per clip in analysis/preguide_{idx}.json."""
import json
import re
from pathlib import Path

import numpy as np

from . import config, db

VERSION = 7
STOP = set("the a an and or but so to of in on at for with is it its it's i im i'm you your we they he she this that "
           "was were be are am do did dont don't just like yeah oh uh um bro what no yes not my me gonna wanna brother bruh "
           "man dude okay ok all got get have has had there here then them him her our out up can can't cant know "
           "really right now one two go going see look let lets let's huh hey yo".split())


def _file(job_id, idx) -> Path:
    return config.job_dir(str(job_id)) / "analysis" / f"preguide_{idx}.json"


def story_ranges(job_id, c: dict) -> list[tuple[float, float]] | None:
    """A story's parts, lengthened when together they are shorter than the job's minimum length (the AI may not use
    footage outside the parts, so a too-short story could never pass). Each part grows on both sides, ends are moved to
    a pause in speech, parts never overlap, at most 10 s per side. None for single-window clips."""
    beats = (c.get("story") or {}).get("beats") or []
    if not beats:
        return None
    rs = [[float(b["start"]), float(b["end"])] for b in beats]
    meta = db.q1("select meta from jobs where id=%s", (str(job_id),))["meta"] or {}
    meta = {**meta, **(c.get("opts") or {})}
    length = meta.get("length")
    total = sum(b - a for a, b in rs) + 3.0  # + a ~3 s cold-open teaser
    need = (length[0] + 2.0 - total) if length else 0.0
    if length and need <= 0:
        return [tuple(r) for r in rs]
    words = _load(config.job_dir(str(job_id)) / "analysis" / "transcript.json", {}).get("words") or []
    # no target length: a little room around every part (to the next pause) so the editor can finish the build-up
    # and the reaction; with a target length: enough to reach it
    pad = min(10.0, need / (2 * len(rs)) + 0.5) if length else 4.0
    order = sorted(range(len(rs)), key=lambda i: rs[i][0])
    for k, i in enumerate(order):
        lo = rs[order[k - 1]][1] + 0.5 if k else 0.0
        hi = rs[order[k + 1]][0] - 0.5 if k + 1 < len(order) else 1e12
        a, b = max(lo, rs[i][0] - pad), min(hi, rs[i][1] + pad)
        # end on a pause: the first gap between words after b (within 2 s), start on a pause before a
        gaps_after = [w["e"] for w, nx in zip(words, words[1:]) if b <= w["e"] <= b + 2.0 and nx["s"] - w["e"] > 0.25]
        gaps_before = [nx["s"] for w, nx in zip(words, words[1:]) if a - 2.0 <= nx["s"] <= a and nx["s"] - w["e"] > 0.25]
        b = min(hi, gaps_after[0] + 0.2) if gaps_after else b
        a = max(lo, gaps_before[-1] - 0.2) if gaps_before else a
        rs[i] = [round(a, 2), round(b, 2)]
    if length:  # parts packed close together cannot grow inwards: extend the outer edges until the minimum fits
        short = length[0] + 8.0 - sum(b - a for a, b in rs)  # headroom: dead-air cuts and snapping shorten the edit
        if short > 0:
            st = db.q1("select result from stages where job_id=%s and name='ingest' and clip_idx=-1", (str(job_id),))
            dur = float((((st or {}).get("result") or {}).get("source") or {}).get("duration") or 1e12)
            first, last = order[0], order[-1]
            grow_back = min(rs[first][0], short / 2)
            grow_fwd = min(dur - rs[last][1], short - grow_back)
            grow_back = min(rs[first][0], short - grow_fwd)  # use the other side if one edge hits the source bounds
            a, b = rs[first][0] - grow_back, rs[last][1] + grow_fwd
            # land the new outer edges in a pause, never inside a word (look a little further out for one)
            before = [nx["s"] for w, nx in zip(words, words[1:]) if a - 3.0 <= nx["s"] <= a + 0.5 and nx["s"] - w["e"] > 0.25]
            after = [w["e"] for w, nx in zip(words, words[1:]) if b - 0.5 <= w["e"] <= b + 3.0 and nx["s"] - w["e"] > 0.25]
            if before:
                a = before[-1] - 0.15
            else:  # no pause nearby: at least start before the word that a would cut
                a = min([w["s"] - 0.05 for w in words if w["s"] < a < w["e"]] or [a])
            if after:
                b = after[0] + 0.15
            else:
                b = max([w["e"] + 0.05 for w in words if w["s"] < b < w["e"]] or [b])
            rs[first][0] = round(max(0.0, a), 2)
            rs[last][1] = round(min(dur, b), 2)
    return [tuple(r) for r in rs]


def beats_of(c: dict, ranges=None) -> list[tuple[float, float, str]]:
    st = (c.get("story") or {}).get("beats") or []
    if st:
        rs = ranges or [(float(b["start"]), float(b["end"])) for b in st]
        return [(float(r[0]), float(r[1]), b.get("role") or "beat") for r, b in zip(rs, st)]
    return [(float(c["start"]), float(c["end"]), "clip")]


def research(job_id, idx: int, c: dict, hint_box: dict | None = None) -> dict:
    """{beats: [{start, end, role}], guide: {...}} for this clip's candidate; computed once, then cached."""
    f = _file(job_id, idx)
    beats = beats_of(c, story_ranges(job_id, c))
    key = [[round(a, 2), round(b, 2)] for a, b, _ in beats]
    if f.exists():
        r = json.loads(f.read_text())
        if r.get("key") == key and r.get("v") == VERSION:
            return r
    out = [{"start": a, "end": b, "role": role} for a, b, role in beats]
    res = {"key": key, "v": VERSION, "beats": out}
    try:
        res["guide"] = build(job_id, c, out)
    except Exception as e:  # noqa: BLE001 - the plan can still be made without it
        res["guide"] = {"error": str(e)[:200]}
    f.write_text(json.dumps(res))
    db.event(str(job_id), "info", "director", f"clip {idx}: edit guide prepared for {len(out)} part(s) before editing", {})
    return res


def _load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _jump(gap: float) -> str:
    """On-screen label for a jump in stream time between two parts."""
    a = abs(gap)
    if a < 90:
        return ""
    amount = f"{a / 3600:.0f} HOURS" if a >= 5400 else ("1 HOUR" if a >= 2700 else f"{max(2, round(a / 60))} MINUTES")
    return f"{amount} LATER" if gap > 0 else f"{amount} EARLIER"


def build(job_id, c: dict, beats: list[dict]) -> dict:
    """The full edit guide for one clip, in source seconds."""
    a = config.job_dir(str(job_id)) / "analysis"
    tl = _load(a / "timeline.json", {})
    comps = {k: np.asarray(v, dtype=float) for k, v in (tl.get("components") or {}).items()}
    score = np.asarray(tl.get("score") or [], dtype=float)
    reactions = tl.get("reactions") or []
    tr = _load(a / "transcript.json", {})
    words = tr.get("words") or []
    af = _load(a / "audio_features.json", {})
    rms, hop = np.asarray(af.get("rms_db") or [], dtype=float), float(af.get("hop") or 0.5)
    rank = _load(a / f"rank_{c.get('idx', 0)}.json", {})
    story = c.get("story") or {}
    sbeats = story.get("beats") or []

    def comp(name, t):
        v = comps.get(name)
        return float(v[int(t)]) if v is not None and 0 <= int(t) < len(v) else 0.0

    def peaks(name, lo, hi, thr, n, gap=4):
        v = comps.get(name)
        if v is None:
            return []
        seg = [(float(v[t]), t) for t in range(max(0, int(lo)), min(len(v), int(hi) + 1)) if v[t] >= thr]
        out = []
        for val, t in sorted(seg, reverse=True):
            if all(abs(t - u) >= gap for _, u in out):
                out.append((val, t))
        return sorted(out[:n], key=lambda x: x[1])

    def loudness(w):
        if not len(rms):
            return 0.0
        i0, i1 = int(w["s"] / hop), int(w["e"] / hop) + 1
        return float(rms[i0:i1].max()) if rms[i0:i1].size else 0.0

    parts, zooms, sfx, texts, cuts = [], [], [], [], []
    for k, b in enumerate(beats):
        lo, hi = b["start"], b["end"]
        sb = next((x for x in sbeats if float(x["start"]) < hi and float(x["end"]) > lo), {})
        part = {"n": k + 1, "start": lo, "end": hi, "role": b.get("role"), "reason": sb.get("reason"), "quote": sb.get("evidence")}
        if k:
            part["jump_label"] = _jump(lo - beats[k - 1]["end"])
        parts.append(part)
        has_cam = bool(c.get("has_facecam"))
        # at most 2 zooms and 1 sound per part, spread out, so every part gets its accents (not all in the first)
        pz = []
        for val, t in peaks("scream", lo, hi, 3.0, 1):
            sfx.append({"at": float(t), "sound": "boom", "why": "the biggest scream / outburst of this part"})
            pz.append({"at": float(t), "target": "facecam" if has_cam else "gameplay", "why": "the biggest outburst of this part"})
        for val, t in peaks("reaction", lo, hi, 2.0, 2):
            if has_cam and len(pz) < 2 and all(abs(z["at"] - t) >= 4 for z in pz):
                pz.append({"at": float(t), "target": "facecam", "why": f"the streamer reacts on camera (movement {val:.1f}x usual)"})
        for val, t in peaks("loud", lo, hi, 1.0, 1):
            if len(pz) < 2 and all(abs(z["at"] - t) >= 4 for z in pz):
                pz.append({"at": float(t), "target": "gameplay", "why": "the loudest action in this part"})
        zooms += pz
        # on-screen text: the loudest short line of the part
        ws = [w for w in words if w["s"] >= lo and w["e"] <= hi]
        lines, cur = [], []
        for w in ws:
            if cur and w["s"] - cur[-1]["e"] > 0.6:
                lines.append(cur)
                cur = []
            cur.append(w)
            if re.search(r"[.!?]$", w["w"].strip()):
                lines.append(cur)
                cur = []
        if cur:
            lines.append(cur)
        def meaningful(ln):  # at least two real words, so "I'MA - BRO." never becomes a callout
            return sum(1 for w in ln if len(re.sub(r"\W", "", w["w"])) > 2 and re.sub(r"\W", "", w["w"].lower()) not in STOP) >= 2
        lines = [ln for ln in lines if 3 <= len(ln) <= 7 and meaningful(ln)]
        if lines:
            best = max(lines, key=lambda ln: np.mean([loudness(w) for w in ln]))
            texts.append({"at": best[0]["s"], "text": " ".join(w["w"] for w in best).strip(" ,-").upper()[:40],
                          "why": "the loudest short line of this part, worth a callout"})
        if len(rms) and ws:
            from .deadair import dead_stretches
            cuts += [{"start": x0, "end": x1, "why": "nobody talks and nothing loud happens"}
                     for x0, x1 in dead_stretches(lo, hi, words, rms.tolist(), hop, reactions)]
    # cold open: the strongest second of the payoff (the last part), shown first as a teaser
    last = beats[-1]
    cold = None
    if len(score):
        i0, i1 = int(last["start"]), min(len(score), int(last["end"]) + 1)
        if i1 > i0:
            t = i0 + int(np.argmax(score[i0:i1]))
            cold = {"start": max(last["start"], t - 1.5), "end": min(last["end"], t + 1.5), "why": "the payoff's peak, shown first to make people stay"}
    # caption words to highlight: the loudest meaningful words
    cand = [w for w in words if any(p["start"] <= w["s"] <= p["end"] for p in parts)
            and len(re.sub(r"\W", "", w["w"])) > 2 and re.sub(r"\W", "", w["w"].lower()) not in STOP]
    from collections import Counter
    freq = Counter(re.sub(r"\W", "", w["w"].lower()) for w in words)
    total = max(1, sum(freq.values()))
    rz = (rms - np.median(rms)) / (np.std(rms) + 1e-6) if len(rms) else rms

    def weight(w):  # loud AND rare in this stream: names, game words, the punchline - not filler
        i0, i1 = int(w["s"] / hop), int(w["e"] / hop) + 1
        loud = float(rz[i0:i1].max()) if len(rz) and rz[i0:i1].size else 0.0
        return loud + 1.5 * np.log(total / (1 + freq[re.sub(r"\W", "", w["w"].lower())]))
    emph = []
    for w in sorted(cand, key=weight, reverse=True):
        clean = re.sub(r"[^\w']", "", w["w"])
        if clean and clean.lower() not in (e.lower() for e in emph):
            emph.append(clean)
        if len(emph) >= 6:
            break
    # time jumps get a whoosh + flash, so the back-and-forth is clear
    trans = [{"at": p["start"], "kind": "flash", "sound": "whoosh", "label": p["jump_label"], "why": "the story jumps in time here"}
             for p in parts if p.get("jump_label")]
    kind = ((c.get("text_moment") or {}).get("kind") or "").lower()
    mood = {"funny": "funny", "rage": "intense", "fail": "funny", "clutch": "epic", "surprise": "suspense"}.get(kind)
    hooks = [h for h in (rank.get("hook"), (c.get("text_moment") or {}).get("hook"), c.get("hook")) if h]
    return {"parts": parts, "cold_open": cold, "zooms": sorted(zooms, key=lambda z: z["at"]),
            "sfx": sfx[:8], "texts": texts[:5], "transitions": trans, "caption_emphasis": emph, "dead_air": cuts[:8],
            "hooks": list(dict.fromkeys(hooks))[:3], "titles": [x for x in (rank.get("title"), story.get("title")) if x][:2],
            "music_mood": mood, "what": rank.get("event") or story.get("summary"), "why": rank.get("why_engaging") or story.get("connection")}


def mark_sent(job_id, idx: int):
    f = _file(job_id, idx)
    r = _load(f, {})
    if r:
        r["sent"], r["guided_plan"] = True, False
        f.write_text(json.dumps(r))


def mark_plan(job_id, idx: int):
    """Called when a plan is accepted: if the director got this guide, the current plan was made with it."""
    f = _file(job_id, idx)
    r = _load(f, {})
    if r:
        r["guided_plan"] = bool(r.get("sent"))
        f.write_text(json.dumps(r))


def prompt_section(res: dict) -> str:
    """The guide as the director's brief (source seconds)."""
    if not res or not res.get("beats"):
        return ""
    g = res.get("guide") or {}
    L = ["\n## Edit guide prepared before editing (follow it; deviate only when the video clearly shows something better, and say why in that element's reason)"]
    for x, p in zip(res["beats"], g.get("parts") or [{}] * len(res["beats"])):
        L.append(f"- Part {p.get('n', '?')} {x['start']:.1f}-{x['end']:.1f}s ({x['role']})."
                 + (f" Starts with a time jump: show on-screen text \"{p['jump_label']}\" for ~1.5 s with a whoosh." if p.get("jump_label") else "")
                 + (f" Why: {p['reason']}" if p.get("reason") else ""))
    if g.get("cold_open"):
        L.append(f"- Cold open: {g['cold_open']['start']:.1f}-{g['cold_open']['end']:.1f}s ({g['cold_open']['why']}).")
    for z in g.get("zooms", []):
        L.append(f"- Zoom ({z['target']}) around {z['at']:.1f}s: {z['why']}.")
    for f in g.get("sfx", []):
        L.append(f"- Sound '{f['sound']}' at {f['at']:.1f}s: {f['why']}.")
    for t in g.get("texts", []):
        L.append(f"- On-screen text \"{t['text']}\" at {t['at']:.1f}s: {t['why']}.")
    for d in g.get("dead_air", []):
        L.append(f"- Dead air {d['start']:.1f}-{d['end']:.1f}s: cut it.")
    if g.get("caption_emphasis"):
        L.append("- Highlight in captions: " + ", ".join(g["caption_emphasis"]) + ".")
    if g.get("hooks"):
        from .hookrules import check_hook, structures
        ok, bad = [], []
        for h in g["hooks"]:
            fails = check_hook(h)[0]
            (bad if fails else ok).append(f'"{h}"' + (f" ({', '.join(structures(h))})" if not fails else f" - breaks the hook rules: {fails[0]}"))
        L.append("- Hook ideas (the hook must pass the hook rules; keep the structure word or punctuation that makes it pass, "
                 "e.g. a trailing '...' for a tease or '?' for a question): " + " | ".join(ok + bad))
    if g.get("music_mood"):
        L.append(f"- Music mood: {g['music_mood']}, quiet under speech.")
    return "\n".join(L) + "\n"
