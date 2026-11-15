"""Dead-air removal inside a clip (the idea behind auto-editor's "first pass"): stretches where nobody speaks AND
nothing loud happens AND the streamer does not react are cut out of the main segments, keeping a short breath on
each side so the cut feels natural. Game action (a loud moment) and on-camera reactions are never cut.
Runs on the AI's edit plan before validation, so effects that fell into a removed stretch are dropped by check()."""
import numpy as np

GAP_MIN = 2.5   # seconds of silence (no words) before a stretch counts as dead air
KEEP = 0.4      # seconds kept on each side of a removed stretch
LOUD_Z = 1.5    # a moment this much louder than usual (robust z) is action, not dead air


def _z(v):
    v = np.asarray(v, dtype=np.float64)
    med = np.median(v)
    return (v - med) / (np.median(np.abs(v - med)) * 1.4826 + 1e-6)


FROZEN_GAP = 1.5   # a silent stretch whose picture is frozen (loading screen, menu) is cut after this much instead


def _frozen_share(a: float, b: float, frozen) -> float:
    if not frozen or b <= a:
        return 0.0
    return sum(max(0.0, min(b, fb) - max(a, fa)) for fa, fb in frozen) / (b - a)


def dead_stretches(seg_start: float, seg_end: float, words: list[dict], rms_db: list[float], hop: float,
                   reactions: list[float], gap_min: float = GAP_MIN, frozen=None) -> list[tuple[float, float]]:
    """Removable (start, end) stretches inside one segment, already shrunk by KEEP on both sides."""
    inside = sorted((w for w in words if w["e"] > seg_start and w["s"] < seg_end), key=lambda w: w["s"])
    edges = [seg_start] + [x for w in inside for x in (w["s"], w["e"])] + [seg_end]
    short = min(gap_min, FROZEN_GAP)
    gaps = [(edges[i], edges[i + 1]) for i in range(0, len(edges) - 1, 2)
            if edges[i + 1] - edges[i] >= gap_min
            or (edges[i + 1] - edges[i] >= short and _frozen_share(edges[i], edges[i + 1], frozen) >= 0.8)]
    loud = _z(rms_db) if len(rms_db) else np.zeros(0)
    out = []
    for a, b in gaps:
        lo, hi = int(a / hop), int(b / hop) + 1
        if len(loud) and loud[lo:hi].size and loud[lo:hi].max() > LOUD_Z:
            continue  # something loud happens: game action or a scream
        if any(a - 1.0 <= r <= b + 1.0 for r in reactions):
            continue  # the streamer reacts on camera
        # the clip's first/last seconds are trimmed only on the inside: never cut right at a segment edge
        ca, cb = a + (KEEP if a > seg_start else 0.15), b - (KEEP if b < seg_end else 0.15)
        if cb - ca >= 1.0:
            out.append((round(ca, 3), round(cb, 3)))
    return out


def cut_dead_air(segments: list[dict], words: list[dict], rms_db: list[float], hop: float, reactions: list[float],
                 min_total: float, gap_min: float = GAP_MIN, frozen=None) -> tuple[list[dict], list[str]]:
    """Split main segments around dead stretches, longest first, never going below min_total seconds."""
    total = sum(s["end"] - s["start"] for s in segments)
    cands = []
    for i, s in enumerate(segments):
        if s.get("role", "main") != "main":
            continue
        for a, b in dead_stretches(s["start"], s["end"], words, rms_db, hop, reactions, gap_min, frozen):
            cands.append((b - a, i, a, b))
    chosen: dict[int, list[tuple[float, float]]] = {}
    removed = 0.0
    for length, i, a, b in sorted(cands, reverse=True):
        if total - removed - length < min_total:
            continue
        chosen.setdefault(i, []).append((a, b))
        removed += length
    if not chosen:
        return segments, []
    out, notes = [], []
    for i, s in enumerate(segments):
        cuts = sorted(chosen.get(i, []))
        if not cuts:
            out.append(s)
            continue
        cur = s["start"]
        for a, b in cuts:
            out.append({**s, "start": cur, "end": a})
            notes.append(f"removed {b - a:.1f}s of dead air at {a:.1f}s")
            cur = b
        out.append({**s, "start": cur, "end": s["end"]})
    return [x for x in out if x["end"] - x["start"] > 0.3], notes
