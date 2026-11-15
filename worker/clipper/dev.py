"""Fast dev loop on a fixed test clip: no Whisper, no Gemini, no n8n, no queue.

    ./dev.sh fixture <job_id> <clip_idx>   snapshot a finished clip as the test fixture (once)
    ./dev.sh render                        draft render (540x960) of data/dev/fixture/edl.json   ~15-30 s
    ./dev.sh render --final                full-quality render + loudness + QC checks
    ./dev.sh replan                        re-validate edl.json (schema, style limits, opening trim), then draft render
    ./dev.sh still 3.5                     one frame at 3.5 s as a PNG                             ~3 s
    ./dev.sh studio                        live preview in Remotion Studio (no rendering)
    ./dev.sh edit <job_id> <clip_idx>      open a finished clip in Remotion Studio (captions NOT burned in: resize captions, add logos, zoom...)
    ./dev.sh polish <in.mp4> <out.mp4>     loudness-normalise a video you rendered from Studio (video is copied, not re-encoded)
    ./dev.sh full                          the real pipeline end to end (n8n + Gemini + review), before calling it done

Edit data/dev/fixture/edl.json (captions, zooms, hook, style...) or the renderer code, then run one of the above.
The fixture lives in data/ (gitignored, never cleaned up by retention)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, media
from .edl import EDL, check
from .render import _base, _blur_bg, build_props

FX = config.DATA / "dev" / "fixture"
OUT = config.DATA / "dev" / "out"
PUB = FX / "public"


def _say(t0, msg):
    print(f"[{time.time() - t0:5.1f}s] {msg}", flush=True)


def _remotion(args: list[str]):
    env = {**os.environ, "REMOTION_DISABLE_TELEMETRY": "1"}
    p = subprocess.run(["npx", "remotion", *args], cwd=config.RENDERER, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        sys.exit(f"remotion failed:\n{(p.stderr or p.stdout)[-2000:]}")


def make_fixture(job_id: str, idx: int):
    from . import db
    clip = db.q1("select * from clips where job_id=%s and idx=%s", (job_id, idx))
    job = db.q1("select * from jobs where id=%s", (job_id,))
    if not clip or not clip["edl"]:
        sys.exit("clip not found or it has no edit plan")
    src = Path(job["source_path"])
    rd = config.job_dir(job_id) / f"render_{idx}"
    if FX.exists():
        shutil.rmtree(FX)
    PUB.mkdir(parents=True)
    end = max(s["end"] for s in clip["edl"]["segments"]) + 5
    if src.stat().st_size < 500 << 20:
        shutil.copy2(src, FX / "source.mp4")
    else:  # long source: keep only what the clip can reach (times stay absolute)
        media.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", f"{end}", "-c", "copy", str(FX / "source.mp4")])
    shutil.copy2(config.job_dir(job_id) / "analysis" / "transcript.json", FX / "transcript.json")
    for sub in ("assets", "sfx"):
        if (rd / "public" / sub).exists():
            shutil.copytree(rd / "public" / sub, PUB / sub)
    (FX / "edl.json").write_text(json.dumps(clip["edl"], indent=1))
    (FX / "edl.original.json").write_text(json.dumps(clip["edl"], indent=1))
    (FX / "candidate.json").write_text(json.dumps(clip["candidate"], indent=1))
    print(f"fixture saved to {FX} (clip {idx} of job {job_id[:8]}: {clip['edl'].get('title')})")


def _load() -> tuple[EDL, dict, dict]:
    if not (FX / "edl.json").exists():
        sys.exit("no fixture yet: run ./dev.sh fixture <job_id> <clip_idx>")
    return (EDL.model_validate(json.loads((FX / "edl.json").read_text())),
            json.loads((FX / "transcript.json").read_text()), json.loads((FX / "candidate.json").read_text()))


def _props(edl: EDL, tr: dict, t0) -> Path:
    seg_key = hashlib.sha256(json.dumps([s.model_dump() for s in edl.segments], sort_keys=True).encode()).hexdigest()[:12]
    stamp = FX / "segments.key"
    if not stamp.exists() or stamp.read_text() != seg_key:
        (PUB / "base.mp4").unlink(missing_ok=True)
    _base(FX / "source.mp4", edl, PUB / "base.mp4", {})
    stamp.write_text(seg_key)
    _say(t0, "cut ready (cached unless the segments changed)")
    bi = media.probe(PUB / "base.mp4")
    props = build_props("dev", 0, edl, bi, tr=tr)
    if edl.layout.mode == "fit":
        _blur_bg(PUB / "base.mp4", PUB / "bg.mp4")
        props["bgSrc"] = "bg.mp4"
    for s in props["sfx"]:
        lib = config.RENDERER / "public" / s["file"]
        if not (PUB / s["file"]).exists() and lib.exists():
            (PUB / "sfx").mkdir(exist_ok=True)
            shutil.copy2(lib, PUB / s["file"])
    missing = [o["file"] for o in props["overlays"] if o["file"] and not (PUB / o["file"]).exists()]
    if missing:
        sys.exit(f"overlay assets missing in the fixture: {missing}. Use emoji already in the fixture or run ./dev.sh full.")
    pp = FX / "props.json"
    pp.write_text(json.dumps(props))
    return pp


def _sheet(video: Path, png: Path):
    d = media.probe(video)["duration"]
    media.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", f"fps=8/{d:.3f},scale=270:-2,tile=8x1",
               "-frames:v", "1", str(png)])


def render(final=False):
    t0 = time.time()
    edl, tr, _ = _load()
    pp = _props(edl, tr, t0)
    OUT.mkdir(parents=True, exist_ok=True)
    name = "final" if final else "draft"
    raw = OUT / f"{name}.raw.mp4"
    _remotion(["render", "src/index.ts", "Clip", str(raw), f"--props={pp}", f"--public-dir={PUB}", "--codec=h264",
               "--audio-codec=aac", "--log=error",
               *(["--crf=17", f"--concurrency={config.render_concurrency()}"] if final else ["--scale=0.5", "--crf=30", "--concurrency=8", "--jpeg-quality=70"])])
    _say(t0, "remotion render done")
    out = OUT / f"{name}.mp4"
    if final:
        from .render import finalize
        finalize(raw, out)
        raw.unlink()
        from .qc import check_file
        r = check_file(out, json.loads(pp.read_text()))
        _say(t0, "QC " + ("PASSED" if r["passed"] else f"FAILED: {', '.join(r['fails'])}"))
        for k, v in r["checks"].items():
            print(f"   {'ok ' if v['ok'] else 'BAD'} {k}: {v['detail']}")
    else:
        raw.rename(out)
    _sheet(out, OUT / f"{name}_sheet.png")
    _say(t0, f"done -> {out}  (frames overview: {OUT / (name + '_sheet.png')})")
    if sys.platform == "darwin" and os.environ.get("DEV_NO_OPEN") != "1":
        subprocess.run(["open", str(out)])


def replan():
    t0 = time.time()
    data = json.loads((FX / "edl.json").read_text())
    cand = json.loads((FX / "candidate.json").read_text())
    from pydantic import ValidationError
    try:
        edl = EDL.model_validate(data)
    except ValidationError as e:
        sys.exit("edl.json is invalid:\n" + "\n".join(f"  {'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
    errors, warnings, edl = check(edl, cand["start"], cand["end"])
    from .gemini import trim_dead_opening
    edl, cut = trim_dead_opening(None, edl, str(FX / "source.mp4"))
    if cut:
        warnings.append(f"opening trimmed by {cut:.2f}s (black/fade frames)")
    for w in warnings:
        print("  warning (auto-fixed):", w)
    if errors:
        sys.exit("edl.json rejected (the AI would get a repair round):\n" + "\n".join("  " + e for e in errors))
    (FX / "edl.json").write_text(json.dumps(edl.model_dump(), indent=1))
    _say(t0, "edit plan valid")
    render()


def still(at: float):
    t0 = time.time()
    edl, tr, _ = _load()
    pp = _props(edl, tr, t0)
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"still_{at:.2f}.png"
    _remotion(["still", "src/index.ts", "Clip", str(png), f"--props={pp}", f"--public-dir={PUB}",
               f"--frame={int(at * 30)}", "--scale=0.5", "--log=error"])
    _say(t0, f"done -> {png}")
    if sys.platform == "darwin" and os.environ.get("DEV_NO_OPEN") != "1":
        subprocess.run(["open", str(png)])


def studio():
    edl, tr, _ = _load()
    pp = _props(edl, tr, time.time())
    print("Remotion Studio on http://localhost:3010 (Ctrl+C to stop). Edits to the renderer code reload live.")
    os.chdir(config.RENDERER)
    os.execvpe("npx", ["npx", "remotion", "studio", "src/index.ts", f"--props={pp}", f"--public-dir={PUB}", "--port=3010"],
               {**os.environ, "REMOTION_DISABLE_TELEMETRY": "1"})


def edit(job_id: str, idx: int):
    rd = config.job_dir(job_id) / f"render_{idx}"
    if not (rd / "props.json").exists() or not (rd / "public" / "base.mp4").exists():
        sys.exit(f"no editable project at {rd} (render the clip first; old jobs may have been cleaned up)")
    (rd / "public" / "logos").mkdir(exist_ok=True)
    props = json.loads((rd / "props.json").read_text())  # older jobs lack the editor fields: add them so the panel shows them
    for k, v in (("captionScale", 1), ("captionOffsetY", 0), ("zoomScale", 1), ("logos", [])):
        props.setdefault(k, v)
    props["hook"].setdefault("enabled", True)
    (rd / "studio_props.json").write_text(json.dumps(props, indent=1))
    (config.RENDERER / "src" / "studio-props.json").write_text(json.dumps(props, indent=1))  # the panel shows these values
    print(f"""Remotion Studio on http://localhost:3010 (Ctrl+C to stop)
 - Props panel (right side): captionScale (1.3 = bigger), captionOffsetY (px), zoomScale (0 = no zooms, 1.5 = stronger),
   zooms[] (start, end, scale, fx, fy), captions.words (edit text/timing), overlays[], hook.text, logos[].
 - Logos: copy a PNG into {rd / 'public' / 'logos'}/ and add {{"file":"logos/me.png","x":0.88,"y":0.08,"w":160}} to logos[].
 - Render with the Render button (top right); the file lands in renderer/out/. Then: ./dev.sh polish renderer/out/<file>.mp4 final.mp4""")
    os.chdir(config.RENDERER)
    os.execvpe("npx", ["npx", "remotion", "studio", "src/index.ts", f"--public-dir={rd / 'public'}", "--port=3010"],
               {**os.environ, "REMOTION_DISABLE_TELEMETRY": "1"})


def polish(src: str, dst: str):
    from .render import finalize
    finalize(Path(src).resolve(), Path(dst).resolve())
    print("done ->", dst)


def full():
    import httpx
    cand = json.loads((FX / "candidate.json").read_text())
    clip = config.INBOX / "dev_fixture.mp4"
    if not clip.exists():  # only the clip's window, so the real run is short
        media.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0, cand['start'] - 2)}", "-i", str(FX / "source.mp4"),
                   "-t", f"{cand['end'] - cand['start'] + 4}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                   "-c:a", "aac", str(clip)])
    r = httpx.post("http://127.0.0.1:5678/webhook/gaming-clipper", headers={"X-Clipper-Token": config.TOKEN}, timeout=30,
                   json={"source_type": "file", "filename": clip.name, "rights_confirmed": True, "clips": 1,
                         "style": json.loads((FX / "edl.json").read_text()).get("style", "viral"), "title": "dev fixture"})
    print(r.status_code, r.text[:200])
    print("Follow it on the dashboard: http://127.0.0.1:8787/  (resubmitting the same file reuses finished stages)")


def main(argv):
    cmd = argv[0] if argv else "help"
    if cmd == "fixture" and len(argv) == 3:
        make_fixture(argv[1], int(argv[2]))
    elif cmd == "render":
        render(final="--final" in argv)
    elif cmd == "replan":
        replan()
    elif cmd == "still" and len(argv) == 2:
        still(float(argv[1]))
    elif cmd == "studio":
        studio()
    elif cmd == "edit" and len(argv) == 3:
        edit(argv[1], int(argv[2]))
    elif cmd == "polish" and len(argv) == 3:
        polish(argv[1], argv[2])
    elif cmd == "full":
        full()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
