"""Precise timing, enforced in code (the AI's timestamps are approximate):
- refine_words: re-time Whisper words from the speech-band energy of the audio (Whisper is often ~0.1 s off per word).
- snap_segments: move every cut to a word gap, preferably a sentence end or a scene change, never inside a word."""
import re
import wave
from pathlib import Path

import numpy as np

HOP = 0.01  # 10 ms analysis frames


def _speech_energy(wav: Path, start: float, end: float) -> tuple[np.ndarray, float]:
    """dB energy in the speech band (300-3400 Hz) for [start, end] of a 16 kHz mono wav, 10 ms frames."""
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        w.setpos(max(0, int(start * sr)))
        raw = w.readframes(max(0, int((end - start) * sr)))
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    win, hop = int(0.025 * sr), int(HOP * sr)
    if len(x) < win:
        return np.zeros(0), start
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop] * np.hanning(win)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = spec[:, (freqs >= 300) & (freqs <= 3400)].sum(axis=1)
    return 10 * np.log10(band + 1e-10), max(0.0, start)


def refine_words(words: list[dict], wav: Path, start: float, end: float) -> list[dict]:
    """Shift each word start to the nearest clear speech onset within -150/+100 ms, and its end to where the
    energy falls. Words keep their order and never overlap. Unclear words keep Whisper's timing."""
    ws = [dict(w) for w in words if w["e"] > start and w["s"] < end]
    if not ws or not Path(wav).exists():
        return ws
    e_db, t0 = _speech_energy(Path(wav), start - 0.5, end + 0.5)
    if not len(e_db):
        return ws
    floor = np.percentile(e_db, 20)
    idx = lambda t: int(round((t - t0) / HOP))  # noqa: E731
    prev_end = -1e9
    for w in ws:
        a, b = idx(w["s"] - 0.15), idx(w["s"] + 0.10)
        seg = e_db[max(0, a):max(0, b)]
        if len(seg) > 3:
            rise = np.diff(seg)
            loud = seg[1:] > floor + 8
            cand = np.where(loud & (rise > 2.5))[0]
            if len(cand):
                k = cand[np.argmin(np.abs(cand - (idx(w["s"]) - max(0, a))))]
                w["s"] = round(t0 + (max(0, a) + k) * HOP, 3)
        w["s"] = max(w["s"], prev_end + 0.01)
        a2, b2 = idx(w["e"] - 0.10), idx(w["e"] + 0.15)
        seg2 = e_db[max(0, a2):max(0, b2)]
        if len(seg2) > 3:
            quiet = np.where(seg2 < floor + 6)[0]
            if len(quiet):
                w["e"] = round(t0 + (max(0, a2) + quiet[0]) * HOP, 3)
        w["e"] = max(w["e"], w["s"] + 0.08)
        prev_end = w["e"]
    return ws


SENT_END = re.compile(r"[.!?…]['\")\]]*$")


def boundaries(words: list[dict], scene_cuts: list[float]) -> dict:
    """Candidate cut points: gaps between words (with how 'final' they are) and scene changes."""
    gaps = []
    for a, b in zip(words, words[1:]):
        g = b["s"] - a["e"]
        if g >= 0.12:
            strength = 3 if SENT_END.search(a["w"]) else 2 if g >= 0.6 else 1
            gaps.append({"t": round((a["e"] + b["s"]) / 2, 3), "after": round(a["e"] + 0.06, 3), "before": round(b["s"] - 0.06, 3), "strength": strength})
    return {"gaps": gaps, "scenes": sorted(float(c) for c in scene_cuts)}


def _inside_word(t: float, words: list[dict]) -> dict | None:
    return next((w for w in words if w["s"] + 0.02 < t < w["e"] - 0.02), None)


def snap_point(t: float, kind: str, words: list[dict], b: dict, reach: float = 1.5) -> tuple[float, str]:
    """kind='start' or 'end'. Prefer sentence ends, then long pauses, then any gap; a scene change wins when no one
    is speaking around it. Returns (new_time, note)."""
    best, best_cost, note = t, None, ""
    for g in b["gaps"]:
        x = g["before"] if kind == "start" else g["after"]
        d = abs(x - t)
        if d > reach:
            continue
        cost = d - 0.35 * g["strength"]
        if best_cost is None or cost < best_cost:
            best, best_cost, note = x, cost, "sentence end" if g["strength"] == 3 else "pause"
    for s in b["scenes"]:
        if abs(s - t) <= 0.5 and not _inside_word(s, words) and not any(abs(w["s"] - s) < 0.15 for w in words):
            if best_cost is None or abs(s - t) - 0.9 < best_cost:
                best, best_cost, note = s, abs(s - t) - 0.9, "scene change"
    if best_cost is None:  # no gap nearby: at least never cut inside a word
        w = _inside_word(t, words)
        if w:
            best = w["s"] - 0.05 if kind == "start" else w["e"] + 0.06
            note = "word edge"
    return round(best, 3), note


def snap_segments(segments: list[dict], words: list[dict], scene_cuts: list[float], win: tuple[float, float]) -> tuple[list[dict], list[str]]:
    """Snap every segment's start/end; drop segments that become too short; never let main segments overlap."""
    b = boundaries(words, scene_cuts)
    out, notes = [], []
    for s in segments:
        s = dict(s)
        ns, n1 = snap_point(s["start"], "start", words, b)
        ne, n2 = snap_point(s["end"], "end", words, b)
        ns, ne = max(win[0] - 1.0, ns), min(win[1] + 1.0, ne)
        if ne - ns >= 0.4:
            if abs(ns - s["start"]) > 0.01:
                notes.append(f"cut {s['start']:.2f}->{ns:.2f}s ({n1 or 'gap'})")
            if abs(ne - s["end"]) > 0.01:
                notes.append(f"cut {s['end']:.2f}->{ne:.2f}s ({n2 or 'gap'})")
            s["start"], s["end"] = ns, ne
        out.append(s)
    mains = sorted((i for i, s in enumerate(out) if s.get("role", "main") == "main"), key=lambda i: out[i]["start"])
    for i, j in zip(mains, mains[1:]):  # snapping may make neighbours touch: keep them apart
        if out[j]["start"] < out[i]["end"]:
            out[i]["end"] = out[j]["start"]
    out = [s for s in out if s["end"] - s["start"] >= 0.4]
    return out, notes


def ends_mid_sentence(seg_end: float, words: list[dict]) -> bool:
    """True when speech continues straight through the cut (no sentence end, next word starts within 0.25 s)."""
    before = [w for w in words if w["e"] <= seg_end + 0.02]
    after = [w for w in words if w["s"] >= seg_end - 0.02]
    if not before or not after:
        return False
    return not SENT_END.search(before[-1]["w"]) and after[0]["s"] - before[-1]["e"] < 0.25


def extend_tail(segments: list[dict], words: list[dict], limit: float, tail: float = 0.6) -> list[dict]:
    """Let the payoff land: the last main segment keeps up to `tail` seconds after its final line (the laugh, the
    reaction, the silence) as long as nobody starts talking in that stretch and it stays inside the source window.
    A clip that stops on the last word feels cut off; a human editor always leaves a beat."""
    out = [dict(s) for s in segments]
    mains = [i for i, s in enumerate(out) if s.get("role", "main") == "main"]
    if not mains:
        return out
    i = max(mains, key=lambda k: out[k]["end"])
    end = out[i]["end"]
    nxt = min((w["s"] for w in words if w["s"] >= end - 0.02), default=None)
    room = min(tail, limit - end, (nxt - 0.08 - end) if nxt is not None else tail)
    if room >= 0.15:
        out[i]["end"] = round(end + room, 3)
    return out
