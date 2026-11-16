"""AI mix: one job, several clips, each with the GTA VI layout that suits it, and neighbours that do not look alike.

  choose()  pure rules: how strongly the streamer reacts in a clip (face movement against the rest of the video) decides
            between the framed cam (reaction) and the widescreen + hook card (action with a punchline). Then variety:
            every allowed layout is used when there are enough clips, and no two neighbours match when there is a choice.
  assign()  reads the job's analysis, calls choose(), stores each clip's layout (+ the reason) in its candidate options,
            which is where the director and the renderer already look for a per-clip layout.
Rules, not another model call: it costs nothing, takes no time and its decision can be read, tested and explained."""
import json
import statistics

from . import config, db

LAYOUTS = ("gta_cam", "gta_hook")
REACTIVE_TYPES = ("commentary", "reaction", "irl", "talking")


def reaction_level(face_motion: list[float] | None, start: float, end: float) -> float:
    """How much the streamer moves in [start, end] compared with the rest of the video: 1.0 = typical, 2.0 = twice as much."""
    if not face_motion:
        return 1.0
    a, b = max(0, int(start)), min(len(face_motion), int(end) + 1)
    inside = face_motion[a:b]
    if not inside:
        return 1.0
    rest = [x for x in face_motion if x > 0]
    base = statistics.median(rest) if rest else 0.0
    mean_in = sum(inside) / len(inside)
    return round(mean_in / base, 2) if base > 0 else 1.0


def _scores(clip: dict, allowed: list[str]) -> dict[str, float]:
    r = clip["reaction"]
    react = min(2.0, r) / 2.0                                  # 0..1
    if (clip.get("content_type") or "") in REACTIVE_TYPES:
        react = min(1.0, react + 0.25)
    out = {}
    for layout in allowed:
        out[layout] = react if layout == "gta_cam" else (1.0 - react) + 0.1 * (clip.get("score", 0) / 100.0)
    return out


def choose(clips: list[dict], allowed: list[str] | None, has_webcam: bool) -> list[dict]:
    """clips: [{idx, reaction, content_type, score}] in play order. Returns [{idx, layout, reason}] in the same order."""
    allowed = [x for x in (allowed or list(LAYOUTS)) if x in LAYOUTS]
    note = ""
    if not has_webcam and "gta_cam" in allowed:
        allowed = [x for x in allowed if x != "gta_cam"] or ["gta_hook"]
        note = " (no webcam found in this video, so no framed cam)"
    if not allowed:
        allowed = ["gta_hook"]
    sc = [_scores(c, allowed) for c in clips]
    pick = [max(s, key=s.get) for s in sc]
    why = ["fits best" for _ in clips]
    n = len(clips)

    def used(layout):
        return sum(1 for p in pick if p == layout)

    # every allowed layout appears at least once when there are enough clips: the clip that loses the least moves
    if n >= len(allowed):
        for layout in allowed:
            if used(layout) == 0:
                cands = [(sc[i][pick[i]] - sc[i][layout], i) for i in range(n) if used(pick[i]) > 1]
                if cands:
                    _, i = min(cands)
                    pick[i], why[i] = layout, "used for variety"
    # no two neighbours alike when there is another layout to give (and it is not that layout's only use)
    if len(allowed) > 1:
        for i in range(1, n):
            if pick[i] == pick[i - 1] and used(pick[i]) > 1:
                alts = [layout for layout in allowed if layout != pick[i]]
                pick[i], why[i] = max(alts, key=lambda layout: sc[i][layout]), "different from the clip before it"
    out = []
    for c, layout, w in zip(clips, pick, why):
        r = c["reaction"]
        if layout == "gta_cam":
            reason = f"the streamer reacts strongly here (face movement {r}x typical): framed cam" if w == "fits best" else f"{w}: framed cam"
        else:
            reason = f"mostly the action, face movement {r}x typical: widescreen + hook card" if w == "fits best" else f"{w}: widescreen + hook card"
        out.append({"idx": c["idx"], "layout": layout, "reason": reason + note})
    return out


def assign(job_id: str) -> list[dict]:
    """Give every selected clip of a 'gta_mix' job its layout. Safe to run again (a retry re-plans the clips)."""
    job = db.q1("select meta from jobs where id=%s", (job_id,))
    meta = (job or {}).get("meta") or {}
    a = config.job_dir(str(job_id)) / "analysis"

    def load(name, default):
        try:
            return json.loads((a / name).read_text())
        except (OSError, ValueError):
            return default

    face = load("facecam.json", {})
    has_webcam = bool(face.get("found") and face.get("box") and not face.get("fullscreen_face"))
    fm = load("face_motion.json", None)
    rows = db.q("select idx, candidate from clips where job_id=%s order by idx", (job_id,))
    clips = []
    for r in rows:
        c = r["candidate"] or {}
        clips.append({"idx": r["idx"], "reaction": reaction_level(fm, c.get("start", 0), c.get("end", 0)),
                      "content_type": c.get("content_type"), "score": c.get("moment_score") or 0})
    plan = choose(clips, meta.get("mix_layouts"), has_webcam)
    for p, r in zip(plan, rows):
        cand = dict(r["candidate"] or {})
        opts = dict(cand.get("opts") or {})
        opts.update(template=p["layout"], layout_reason=p["reason"])
        cand["opts"] = opts
        db.q("update clips set candidate=%s where job_id=%s and idx=%s", (db.J(cand), job_id, r["idx"]))
        db.event(job_id, "info", "layout", f"clip {r['idx']}: {p['reason']}", {"layout": p["layout"]})
    return plan
