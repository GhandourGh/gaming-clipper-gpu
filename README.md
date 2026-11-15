# Gaming Clipper

Turns a gaming video you own into edited vertical (1080×1920) clips: n8n orchestrates, a local Python worker does the heavy work, Gemini ranks moments and plans the edit, Remotion renders. Status and known gaps: [PROGRESS.md](PROGRESS.md). Design: [docs/PLAN.md](docs/PLAN.md).

## Run it

1. Start the worker (also starts its Postgres in Docker):
   ```bash
   cd gaming-clipper/worker && ./run.sh
   ```
2. Start the owner dashboard in a second terminal:
   ```bash
   cd gaming-clipper/dashboard && ./run.sh
   ```
   Open http://127.0.0.1:3001. The dashboard keeps the worker token server-side and provides job creation, live progress, clip review, QC results, and cost reporting.
3. n8n must be running (`docker start n8n`, editor at http://localhost:5678). Workflows: **Gaming Clipper — MVP**, **— Assets**, **— Errors** (published).
4. Put your video in `gaming-clipper/inbox/`, then submit it:
   ```bash
   curl -X POST localhost:5678/webhook/gaming-clipper -H "X-Clipper-Token: $(grep ^CLIPPER_TOKEN= gaming-clipper/.env | cut -d= -f2)" -H 'content-type: application/json' -d '{"source_type":"file","filename":"my_video.mp4","rights_confirmed":true}'
   ```
   For YouTube (only your own or CC-licensed videos), use `{"source_type":"youtube","url":"https://www.youtube.com/watch?v=...","rights_confirmed":true}`.
5. Telegram sends a summary and each clip. Approve or reject clips in the dashboard or on the results page linked in the message. Nothing is ever published automatically.

## How it works

`inbox/` → **ingest** → **pass 1** (local: Whisper words, loudness, scream-band energy, scene cuts, facecam detection → candidate windows) → **pass 2** (Gemini watches 360p proxies of the candidates and ranks them; weak ones are dropped) → **Creative Director** (Gemini writes an Edit Decision List, validated against `worker/clipper/edl.py`) → **assets** (licensed emoji/images + original SFX, licences logged) → **render** (ffmpeg pre-cut → Remotion → loudness-normalised H.264) → **QC** → **human review**.

Every stage is checkpointed in Postgres and on disk, so a failed render never re-runs analysis, and resubmitting a video reuses finished work.

## Files
- `worker/`: FastAPI service (`clipper/main.py` API, `analysis.py`, `gemini.py` prompts and schemas, `edl.py`, `render.py`, `qc.py`, `assets.py`).
- `dashboard/`: Next.js owner dashboard. It proxies worker and n8n requests server-side so the shared token is never exposed to the browser.
- `renderer/`: Remotion composition (`src/Clip.tsx`) and the original SFX pack (`public/sfx`).
- `n8n/build_workflows.py`: generates the three workflows (output goes to the gitignored `data/n8n_build/` because it contains your Telegram chat ID).
- `data/`: jobs, caches, outputs (gitignored). `.env`: secrets (gitignored).

## Security
The worker binds to 127.0.0.1 and needs `X-Clipper-Token` (stored in n8n as the credential "Clipper worker token"). The webhook requires the same header. There are no public endpoints and no tunnel. The Gemini key stays in n8n's credential store.
