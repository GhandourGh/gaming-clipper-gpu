# Gaming Clipper MVP: Plan & Build Brief

**Status:** Plan, awaiting owner approval. Written 2026-11-14.
**Branch:** `feat/gaming-clipper`. All new code lives in `gaming-clipper/`.

## 1. What exists today (inspected)

| Item | Finding |
|---|---|
| n8n | Container `n8n`, v2.47.3, SQLite volume `n8n_data`, `127.0.0.1:5678` only. `N8N_EDITOR_BASE_URL` points to an old `trycloudflare.com` tunnel that is **not running** (keep it that way). |
| n8n credentials | Gmail OAuth2, Telegram API, Postgres, Google Gemini (PaLM) API, OpenWeatherMap. |
| Existing workflows | Personal AI Digest (published), My workflow, daily weather. **Do not touch these.** |
| Host | Apple M1, 16 GB RAM, ~93 GB free disk. No NVIDIA GPU. |
| Tools on host | ffmpeg/ffprobe 8.0.1, yt-dlp 2026.06.09, Python 3.12 + uv, Node 25, cloudflared, az CLI, gh. |
| Docker | Desktop running. Phase 1 compose stack (Postgres on 5433) is unrelated; don't reuse its DB. |

**Missing:** licensed asset API keys (optional: Pixabay, Freesound), a short **authorized** test video, and (optional) an Azure Blob storage account.

## 2. Shortest reliable path

**Key decision: run the heavy worker natively on the Mac, not in the cloud.**
The M1 is free compute, already has ffmpeg, and runs Whisper on Apple Silicon fast enough for an MVP. Docker on macOS can't use the GPU/Metal, so the worker runs natively (uv-managed venv, launched by a script); only its Postgres runs in Docker. Cloud rendering (Azure Container Apps Jobs / Modal) comes later, when it's a SaaS, behind the same HTTP API.

```
n8n (orchestrator, :5678)                    clipper-worker (FastAPI, native, 127.0.0.1:8787)
──────────────────────────                   ─────────────────────────────────────────────
Form Trigger ──► validate ──► POST /jobs ──► job row (Postgres) + idempotency key
                                             ingest: file upload | owned-YouTube URL
Wait/poll loop ◄── GET /jobs/{id} ◄───────── pass 1 (cheap, local): whisper words,
                                             loudness/peaks, scene cuts, facecam box
Gemini: rank candidates (pass 2) ◄──────────  candidate windows + low-res proxy clips
Gemini: Creative Director → EDL JSON ──────► POST /edl/validate (Pydantic schema)
Sub-workflow: Assets ───────────────────────► asset manifest (source + licence per item)
                         POST /render ─────► Remotion render per clip (1080×1920 MP4)
                         GET  /qc     ─────► ffprobe/blackdetect/ebur128/caption bounds
Telegram: preview + Approve/Reject (manual creative review) → results page / links
```

n8n never holds video bytes; it passes job IDs, JSON and file URLs only.

## 3. Stage-by-stage design

| Stage | Implementation | Notes |
|---|---|---|
| 1. Input | n8n **Form Trigger** (local only): YouTube URL *or* file upload, plus required checkbox "I own this video or have rights to edit it". URL validated by regex + YouTube oEmbed metadata (no key needed). | **Retrieval:** default = file upload (e.g. downloaded from your own YouTube Studio). Optional yt-dlp path for **your own / CC-licensed** videos only: no cookies, no DRM or login bypass; it fails cleanly on restricted videos. Job ID = UUID; idempotency key = sha256 of the file, or the video ID. Twitch: interface stub only. |
| 2. Analysis pass 1 (cheap) | Worker: `mlx-whisper` (or faster-whisper int8) word timestamps; `ffmpeg ebur128` + RMS for loudness spikes and screams; `ffmpeg scdet` for scene cuts; OpenCV YuNet face detector on sampled frames to find the facecam rectangle; keyword cues from the transcript ("no way", laughter tokens). | Produces scored candidate windows (20–90 s, with buildup before the peak). Results are cached per job, so a failed render never re-runs analysis. |
| 3. Highlight detector (pass 2) | n8n Gemini node (existing credential) gets the top ~8 windows: 360p proxy clip + transcript slice + signal scores. It returns strict JSON (start, end, event, why_engaging, hook, duration, confidence, quality, context). | Overlap removal (IoU > 0.3) in the worker. Quality threshold: fewer, better clips. Scores are a ranking, not virality predictions. |
| 4. Creative Director | Gemini with a system prompt from an editor's playbook, output constrained to the **EDL JSON Schema** (`schemas/edl.schema.json`, mirrored by a Pydantic model). | EDL: `segments` (keep/cut ranges), `layout` (split facecam/gameplay or full-crop + crop box), `camera` (zoom/punch-in keyframes with a reason), `captions` style, `overlays` (asset refs + timing), `sfx`, `transitions`, `music` (track + ducking), `hook_text`. Every effect carries a `reason` field. Invalid EDL → one repair retry → fail with the error. |
| 5. Assets (sub-workflow) | Licensed sources only: **Openverse API** (CC images, no key), **Pixabay** (images/video/music, free key), **Freesound** (CC0 filter, free key), **Noto Emoji** (Apache 2.0) for reaction icons. A bundled starter pack of CC0 SFX (whoosh, boom, ding, record scratch). Image generation only when the EDL flags `needs_generated: true`; off by default. | Every asset is logged with URL, licence and author in `assets_manifest.json` for each clip. No copyrighted memes, music or footage. Cached by query hash for reuse. |
| 6. Render | **Remotion** project (`renderer/`): split-layout composition, keyframed zooms, word-synced animated captions (pop and highlight), overlay and emoji animations, SFX, music ducking. FFmpeg pre-cuts the source and does a jump-cut silence removal first, so Remotion only reads short clips. | Remotion is free for individuals and companies with up to 3 people (check before SaaS). Renders locally; per-clip render artefacts are cached and re-renders re-use them. |
| 7. QC | Worker checks: 1080×1920, fps, A/V duration drift < 100 ms, `blackdetect`, `silencedetect`, true peak ≤ −1 dBTP / loudness ~−14 LUFS, caption boxes inside the safe area (from the EDL), all asset files present, file decodes end to end. | Recoverable failures are retried 2× with backoff. **Telegram review with Approve/Reject buttons** is required before "approved". |
| 8. Delivery | Clips are stored in `gaming-clipper/output/{job}/` and served by the worker at `127.0.0.1:8787/files/...` (local). Telegram gets the MP4 (bot upload limit is 50 MB) plus a summary: title, description, timestamps, scores, durations, cost. A simple local results page at `/jobs/{id}/view`. | Optional later: Azure Blob with short SAS links (uses credits, ask first). Files are cleaned up after 7 days. **No auto-publishing.** |

## 4. Reliability

- Postgres tables `jobs`, `stages`, `artifacts`, `costs`, `events`: per-stage status, so every stage is resumable.
- Async: the worker runs stages in a background queue (one at a time; the M1 can't run parallel renders). n8n polls with Wait + IF, with a timeout per stage.
- Retries with exponential backoff on Gemini 429/5xx (n8n node retry settings) and on worker HTTP calls.
- Structured JSON logs; an n8n Error Trigger workflow sends Telegram alerts.
- Cost tracking: Gemini token counts × the price table → `costs`, summed on the job.
- Secrets stay in the n8n credential store and the worker's `.env` (gitignored). The worker binds to 127.0.0.1 only. A shared header token is required between n8n and the worker. No public endpoints.

## 5. Cost (to verify during build)

- Compute, Whisper, FFmpeg, Remotion: **€0** (local).
- Gemini: free tier first. **Check current Flash limits and that free-tier data may be used for training**, before sending private footage. Paid Flash estimate ≈ $0.01–0.10 per 1-hour VOD with two-pass analysis.
- Asset APIs: free keys. Azure: €0 unless Blob is enabled.

## 6. Decisions needed from the owner

1. **YouTube retrieval:** file upload as the main path, with yt-dlp only for your own or CC-licensed videos (recommended). Or file upload only.
2. **Test video:** provide a short (2–10 min) gaming video you own, or approve a specific CC-BY video.
3. **Gemini:** OK to use the free tier for test footage, given the data-use terms?
4. **Optional keys:** Pixabay and Freesound free API keys (you create them; enter them in n8n credentials yourself).

## 7. Build order & definition of done

1. Worker skeleton + Postgres + job API + ingest (upload) → tested.
2. Pass-1 analysis on the test video → candidates JSON reviewed.
3. n8n workflow "Gaming Clipper — MVP" (new; existing ones untouched): form → job → poll → Gemini ranking.
4. EDL schema + Creative Director + validation.
5. Remotion renderer: layout, zooms, captions first; then SFX, overlays, music.
6. Asset sub-workflow.
7. QC + Telegram review + results page.
8. Real end-to-end run on the test video; fix failures; record actual timings and cost.
9. README: how to run, limitations, quality-improvement plan.

**Done =** one real end-to-end job produces ≥1 downloadable 1080×1920 MP4 that passes QC, with a Telegram review message. Every placeholder is marked as such. Tests for the worker (schema, idempotency, QC functions).

**Note:** your `ROADMAP-90D.md` locks phases (currently Phase 1). This project is outside it; log it in the Change Log or `PARKING_LOT.md` if you're changing course.
