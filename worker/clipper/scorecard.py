"""Scorecard: measure whether a change made the clips better, instead of judging by feel.

Reads what finished jobs already leave on disk (render props, AI review score, auto-reframe path) and reports, per
clip and in total: AI review score, effect density (restraint), camera movement and ending room. Save a snapshot
before a change, run a few jobs, then compare:

    cd worker && uv run python -m clipper.scorecard --save before
    ... change something, re-run the same 3 source videos ...
    uv run python -m clipper.scorecard --since 2026-11-16 --compare before

Restraint rule of thumb: more than ~3 effects (zoom + sound + overlay + transition) per 10 s reads as over-edited."""
import argparse
import json
import statistics
from pathlib import Path

from . import config


def _load(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def clip_metrics(job_dir: Path, idx: int) -> dict | None:
    rd = job_dir / f"render_{idx}"
    props = _load(rd / "props.json")
    if not props:
        return None
    dur = max(1.0, float(props.get("duration") or 0))
    n = {k: len(props.get(k) or []) for k in ("zooms", "sfx", "transitions", "overlays")}
    effects = sum(n.values())
    review = _load(job_dir / "analysis" / f"review_{idx}.json") or {}
    rf = _load(rd / "reframe.json") or {}
    path = rf.get("path") or []
    cam = {"cam_moves": 0, "cam_travel": 0.0}
    if len(path) > 1:
        from .reframe import metrics
        m = metrics([tuple(k) for k in path], dur)
        cam = {"cam_moves": m["moves"], "cam_travel": m["travel"]}
    words = (props.get("captions") or {}).get("words") or []
    tail = round(dur - max((w["e"] for w in words), default=dur), 2)  # room after the last spoken word
    return {"job": job_dir.name[:8], "clip": idx, "style": props.get("style"), "layout": (props.get("layout") or {}).get("mode"),
            "dur": round(dur, 1), **n, "effects_per_10s": round(effects / dur * 10, 2), **cam, "end_room_s": tail,
            "review": review.get("final_score"), "mtime": (rd / "props.json").stat().st_mtime}


def collect(since: float = 0.0) -> list[dict]:
    rows = []
    for jd in sorted(config.DATA.glob("jobs/*")) if hasattr(config, "DATA") else []:
        for rd in sorted(jd.glob("render_*")):
            try:
                idx = int(rd.name.split("_")[1])
            except (IndexError, ValueError):
                continue
            m = clip_metrics(jd, idx)
            if m and m["mtime"] >= since:
                rows.append(m)
    return rows


def summary(rows: list[dict]) -> dict:
    def avg(key):
        v = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(statistics.mean(v), 2) if v else None
    return {"clips": len(rows), "review_avg": avg("review"), "effects_per_10s": avg("effects_per_10s"),
            "over_edited": sum(1 for r in rows if r["effects_per_10s"] > 3), "cam_moves_avg": avg("cam_moves"),
            "end_room_avg_s": avg("end_room_s"), "dur_avg_s": avg("dur")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only clips rendered on/after this date (YYYY-MM-DD)")
    ap.add_argument("--save", help="save this summary as a named baseline")
    ap.add_argument("--compare", help="compare against a saved baseline")
    a = ap.parse_args()
    import datetime
    since = datetime.datetime.fromisoformat(a.since).timestamp() if a.since else 0.0
    rows = collect(since)
    for r in rows:
        print(f"{r['job']} #{r['clip']} {r['style']:<9} {str(r['layout']):<7} {r['dur']:>5}s  fx/10s {r['effects_per_10s']:<5} "
              f"z{r['zooms']} s{r['sfx']} t{r['transitions']} o{r['overlays']}  cam {r['cam_moves']:<3} end {r['end_room_s']:>4}s  review {r['review']}")
    s = summary(rows)
    print("\nSUMMARY", json.dumps(s))
    store = config.DATA / "scorecards"
    if a.save:
        store.mkdir(exist_ok=True)
        (store / f"{a.save}.json").write_text(json.dumps(s, indent=1))
        print(f"saved baseline '{a.save}'")
    if a.compare:
        base = _load(store / f"{a.compare}.json")
        if not base:
            print(f"no baseline named '{a.compare}'")
            return
        print(f"\nvs '{a.compare}':")
        for k, v in s.items():
            b = base.get(k)
            if isinstance(v, (int, float)) and isinstance(b, (int, float)):
                print(f"  {k:<18} {b} -> {v}  ({v - b:+.2f})")


if __name__ == "__main__":
    main()
