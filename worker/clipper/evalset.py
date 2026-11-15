"""Regression set for moment picking. Run before and after changing signals or weights:

    cd worker && uv run python -m clipper.evalset

Each case in worker/evals/cases/*.json describes a synthetic source (length, quiet-talk level, planted moments, an optional
ad break or replay heatmap). The audio features are generated from that description, run through `score_timeline` and
`pick_candidates`, and the result is checked against the case's expectations: every planted moment is inside a candidate
(recall), no candidate overlaps an ad break, the count is inside bounds. `report(job_id)` gives the same kind of numbers for a
real finished job (counts, durations, coverage) so changes can be compared on real footage too.
"""
import json
import sys
from pathlib import Path

import numpy as np

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


def synth(case: dict) -> tuple[dict, dict, dict]:
    """(feats, transcript, extra) for a case description. Deterministic."""
    n = int(case["duration"])
    rng = np.random.default_rng(case.get("seed", 0))
    hop = 0.5
    base = case.get("talk_db", -28.0)
    rms = base + rng.normal(0, 0.8, n * 2)
    hf = np.abs(rng.normal(0.2, 0.05, n * 2))
    for m in case.get("moments", []):  # a scream: loud and high-frequency for a few seconds
        a, b = int(m["at"] * 2), int((m["at"] + m.get("len", 4)) * 2)
        rms[a:b] += m.get("boost_db", 14)
        hf[a:b] = 0.9
    ad = case.get("ad")
    if ad:  # an ad break is loud and flat, so it would score like a moment without the mask
        a, b = int(ad[0] * 2), int(ad[1] * 2)
        rms[a:b] = base + 12
        hf[a:b] = 0.6
    extra = {}
    if ad:
        mask = np.zeros(n, bool)
        mask[int(ad[0]):int(ad[1])] = True
        extra["ads"] = mask
    if case.get("heat"):
        h = np.zeros(n)
        for a, b in case["heat"]:
            h[int(a):int(b)] = 1.0
        extra["heat"] = h
    words = []
    for t in range(0, n, 2):  # steady chat so speech rate is not a signal
        words.append({"s": float(t), "e": t + 0.4, "w": "ok"})
    feats = {"hop": hop, "rms_db": rms.tolist(), "hf_ratio": hf.tolist()}
    tr = {"words": words, "segments": [{"s": float(t), "e": t + 6.0, "text": "okay so"} for t in range(0, n, 6)]}
    return feats, tr, extra


def run_case(case: dict) -> dict:
    from .analysis import pick_candidates, score_timeline
    feats, tr, extra = synth(case)
    n = int(case["duration"])
    tl = score_timeline(n, feats, [], tr, extra=extra)
    cands = pick_candidates(n, tl, tr, case.get("max_candidates", 8))
    hit = []
    for m in case.get("moments", []):
        t = m["at"] + m.get("len", 4) / 2
        hit.append(any(c["start"] <= t <= c["end"] for c in cands))
    ad = case.get("ad")
    ad_overlap = bool(ad) and any(c["start"] < ad[1] and c["end"] > ad[0] and c["peak_time"] >= ad[0] and c["peak_time"] < ad[1]
                                  for c in cands)
    exp = case.get("expect", {})
    checks = {
        "recall": (sum(hit) / len(hit) if hit else 1.0) >= exp.get("recall_min", 1.0),
        "no_ad_peak": not ad_overlap,
        "count": exp.get("candidates_min", 1) <= len(cands) <= exp.get("candidates_max", 99),
    }
    return {"name": case.get("name"), "candidates": len(cands), "recall": f"{sum(hit)}/{len(hit)}", "checks": checks,
            "ok": all(checks.values())}


def load_cases() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(CASES.glob("*.json"))]


def report(job_id: str) -> dict:
    """Numbers for a real job's candidates: how many, how long, how much of the source they cover, how many were selected."""
    from . import config
    a = config.JOBS / job_id / "analysis"
    cands = json.loads((a / "candidates.json").read_text())
    ranks = [json.loads(p.read_text()) for p in sorted(a.glob("rank_*.json"))]
    lens = [c["end"] - c["start"] for c in cands]
    return {"candidates": len(cands), "ranked": len(ranks), "median_len": round(float(np.median(lens)), 1) if lens else 0,
            "signals_used": sorted({k for c in cands for k in c.get("signals", {})}),
            "high_quality": sum(1 for r in ranks if r.get("quality", 0) >= config.MIN_QUALITY)}


def main() -> int:
    results = [run_case(c) for c in load_cases()]
    for r in results:
        print(("PASS " if r["ok"] else "FAIL ") + f"{r['name']}: {r['candidates']} candidates, recall {r['recall']}, {r['checks']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
