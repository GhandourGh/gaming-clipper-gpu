# Gaming Clipper on the GPU — Implementation Plan

Owner's goals (2026-11-16): every processing step runs on the Runpod GPU Pod, nothing runs on the MacBook, a new job finishes
fast and never gets stuck (n8n, Remotion, Docker). n8n may stay local but must not be in the job's critical path.
Pods may be started for tests and must be stopped right after.

## Target layout

```
Phone / browser ──HTTPS──> https://<pod-id>-8787.proxy.runpod.net   (Runpod proxy, only exposed app port)
                              │
                     Pod (RTX A5000, /workspace volume)
                     ├─ dashboard  (Next.js, :8787, access-key login)  ──token──> worker (127.0.0.1:8788, not exposed)
                     ├─ worker     (FastAPI + job engine: ingest → analyze → plan → direct → render → qc → review → finish)
                     │     ├─ faster-whisper on CUDA, ffmpeg NVENC (static build with libass), Gemini called directly
                     │     ├─ Telegram notifications sent directly
                     │     └─ idle auto-stop (runpodctl / pod-scoped key)
                     ├─ PostgreSQL (127.0.0.1:5435, data on /workspace)
                     └─ supervisor loops (restart worker / dashboard / Postgres on crash), started by the Pod start command
Mac: nothing required. Local n8n stays installed; the clipper workflows are unpublished (not needed).
```

## Phases

### Phase 1 — Worker-owned pipeline (removes n8n from the critical path)
- `pipeline.py`: stages `plan` (transcript score, screen + judge ranking with bounded parallel Gemini calls, select,
  trend hints), `direct` (director + EDL repair loop, assets, music, enqueue render), `review` (moderation + AI review,
  one bounded redo), `finish` (job summary, cleanup, notification). Gemini via HTTPS with retry/backoff on 429/5xx, then
  the existing OpenRouter/Groq rescue.
- Queue lanes: `ingest` (downloads), `gpu` (analyze, render, qc), `llm` (plan, direct, review, finish, 3 threads), so a
  slow download or LLM call never blocks rendering. Atomic stage claim. Hooks: analyze→plan, qc→review, terminal clip
  failure→finish.
- Watchdog (every 30 s): resumes jobs left in `analyzed`/`ranking`/`selected` with nothing queued (old n8n jobs
  included), finishes jobs whose clips are all terminal. Per-stage time limits recorded; stale `running` stages are
  re-queued at startup.
- Live chunks, restart, regenerate and watch/cleanup use the same engine (no localhost:5678 calls).
- Failure kinds on clips (`render_failed`, `qc_failed`, `direct_failed`, …); QC never runs on a missing file.

### Phase 2 — GPU render and analysis
- NVENC everywhere ffmpeg encodes on Linux (render, proxies, review files, guide, finalize fallbacks) through one helper.
- Static ffmpeg (BtbN GPL build: NVENC + libass + drawtext) on the volume; preflight proves `h264_nvenc` encodes a frame.
- Remotion is not used on the GPU worker (`RENDER_CPU_FALLBACK=0`); a fast-render failure is `render_failed` with the
  real ffmpeg error. Engine, encoder and render seconds stored per clip and shown in the dashboard.
- faster-whisper large-v3-turbo on CUDA, loaded once per process (already cached), warmed at startup.

### Phase 3 — Pod runtime and reliability
- `scripts/pod-start.sh` (Pod start command): updates code from GitHub, restores ffmpeg/Node/venv/Postgres from the
  volume, builds the dashboard when the commit changes, starts supervisor loops, writes `boot.log`.
- `/health` (liveness) and `/ready` (Postgres, CUDA/GPU name, NVENC test encode, libass, Whisper device, disk, keys).
- Admin endpoints (token-only): `/admin/update` (pull + restart), `/admin/env` (allow-listed keys into
  `/workspace/clipper.env`), `/admin/logs`.
- Idle auto-stop: queue empty, no recording, no dashboard use for `IDLE_STOP_MIN` (default 30) → stop the Pod.

### Phase 4 — Dashboard on the Pod
- Access-key login (dashboard password or the existing worker token) replaces the localhost-only auto-cookie.
- Submit/restart/publish call the worker directly. Job view shows engine, encoder, CPU-fallback warning, failure kind,
  render seconds. GPU page shows live `/ready` data (GPU, encoder, disk, uptime, idle-stop timer) instead of a static card.

### Phase 5 — Deploy, test, shut down
- Push to GitHub, start the A5000, bootstrap once from the Jupyter terminal, set the Pod start command, move the Gemini
  and Telegram keys from local n8n into the Pod env (never printed), run an end-to-end job, record stage timings and GPU
  evidence, stop the Pod.
- Mac: unpublish clipper workflows in local n8n; stop the Mac worker and dashboard launch agents.

## Out of scope for this pass (documented as limitations)
Redis queue (Postgres + in-process lanes is enough for one GPU), object storage / signed URLs (the Pod volume is the
store), remote n8n, Twitch chat-spike detection, social publishing.
