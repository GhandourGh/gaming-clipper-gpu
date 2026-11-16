# Remote GPU Gaming Clipper — Full Architecture Audit and Implementation Plan

## Instructions to Claude: re-check everything before changing code

Before implementing this plan, perform a fresh, repository-wide audit of the current application and the live deployment configuration.

Do not assume this document is complete. Treat it as a starting specification.

You must:

1. Inspect every relevant directory, service, API route, worker stage, queue, renderer, dashboard flow, n8n workflow, database schema, startup script, Docker/Runpod configuration, and environment file.
2. Compare the repository with the currently deployed GPU worker and dashboard.
3. Identify anything missing from this document that could affect speed, reliability, GPU usage, storage, security, cost, resumability, or user experience.
4. Record those missing items in an `AUDIT_GAPS.md` file before implementation.
5. Explain which findings are confirmed by code, which are inferred from behavior, and which require a runtime test.
6. Build a dependency-aware implementation plan before making changes.
7. Implement in small verified phases. Do not redesign unrelated parts of the product.
8. Test each phase locally and on the remote GPU worker.
9. Do not claim that a task is GPU-accelerated unless logs or runtime inspection prove GPU use.
10. Do not silently fall back from GPU rendering to a slow CPU renderer. If a fallback is retained for compatibility, expose it clearly in the UI and logs.

At the end, report:

- What was found during the audit
- What was missing from this document
- What was changed
- What was tested
- Actual timings before and after
- Which stages used the GPU
- Which stages still used the CPU and why
- Remaining limitations
- Exact commands or actions needed to deploy and operate the system

The end goal is that the MacBook is only a user interface and control surface. In normal operation, ingestion, downloading, analysis, AI-assisted moment detection, edit planning, asset preparation, rendering, QC, storage, and job orchestration must happen on the remote infrastructure.

---

## 1. Product goal

This application turns owned or authorized gaming footage, YouTube videos, Twitch VODs, and monitored streams into downloadable vertical clips.

The desired user experience is:

1. Open the dashboard from a phone or browser.
2. Paste a YouTube/Twitch link or upload a file.
3. Choose a reusable template and clip settings.
4. Start the job.
5. The remote GPU infrastructure downloads, analyzes, finds moments, creates edit plans, renders, checks, and stores the clips.
6. The user downloads the finished clips and posts them manually.

Automatic social publishing is deliberately out of scope for the first production version. TikTok drafts/private uploads may be added later.

The system must support both:

- Short highlight moments
- Longer complete-story clips compressed into a short-form edit

The system must support reusable layouts, especially:

- GTA 6 — Gameplay Focus
- Gameplay-only
- Gameplay plus facecam
- Split gameplay and facecam
- Multi-person facecam layouts
- Future game-specific templates

---

## 2. Current architecture to audit

The current application contains:

- A Next.js dashboard
- A Python FastAPI worker
- PostgreSQL for job state
- n8n for orchestration and notifications
- A Remotion renderer
- A fast FFmpeg renderer intended to use NVENC on Linux
- YouTube/Twitch ingestion code
- Live stream monitoring code
- A Runpod GPU Pod deployment
- Tailscale/private access for the dashboard

The current high-level flow is approximately:

```text
Phone/browser
    ↓
Mac-hosted dashboard
    ↓
Mac-hosted n8n
    ↓
Runpod worker
    ├── PostgreSQL
    ├── ingest/download
    ├── Whisper/transcription
    ├── moment analysis
    ├── AI ranking and edit planning
    ├── FFmpeg/NVENC or Remotion rendering
    └── QC
```

This is a useful MVP, but it is not yet a fully remote production architecture.

---

## 3. Confirmed architectural problems to investigate and fix

### 3.1 Rendering fallback is too slow

The fast renderer attempts to use FFmpeg and NVIDIA NVENC. When the GTA layout filter graph fails, the system falls back to Remotion.

Remotion starts Chrome and is substantially slower. It also depends on Node and `npx` being present in the worker process environment.

Required changes:

- Make NVENC the production renderer.
- Normalize all video inputs before crop and overlay operations.
- Make the GTA template filter graph deterministic.
- Test gameplay-only, facecam, logo, captions, and multi-input layouts separately.
- Record the selected render engine in the job and clip records.
- Expose actual encoder and GPU information in job logs.
- Do not silently fall back to Remotion.
- If compatibility fallback remains, label it as `CPU fallback` in the dashboard.

### 3.2 Worker startup is not supervised reliably

The worker has been manually restarted with shell commands. This can lose:

- `CLIPPER_DB_PASSWORD`
- `CLIPPER_TOKEN`
- `PATH`
- Node and `npx`
- `FFMPEG_FULL`
- `PYTHONPATH`

Required changes:

- Create one canonical startup script.
- Load `/workspace/clipper.env` every time.
- Set all required paths inside the process, not only in an interactive shell.
- Add a supervisor or container restart policy.
- Add startup validation for Python, PostgreSQL, Node, npm/npx, FFmpeg, NVENC, and CUDA.
- Add `/health` and `/ready` endpoints.
- Make the Pod restart automatically after a process crash.

### 3.3 n8n currently controls too much of the critical pipeline

n8n currently performs long wait/poll loops and controls transitions after analysis. This can cause a job to remain in `selected` or another intermediate state when an execution is interrupted.

Required changes:

- Move core stage orchestration into the worker/job service.
- Keep n8n for notifications, scheduled watchers, approvals, and optional integrations.
- Make every stage resumable from PostgreSQL.
- Make retries and timeouts worker-owned.
- Add explicit cancellation and retry APIs.
- Ensure a restart resumes the exact unfinished stage without redoing completed work.

### 3.4 The in-process queue does not scale

The current Python queue is process-local and effectively single-worker.

Required changes:

- Introduce Redis plus a durable task queue, or a PostgreSQL-backed queue as an interim step.
- Separate ingest, analysis, render, and QC workloads.
- Enforce GPU concurrency explicitly.
- Allow multiple clips from one job to render sequentially or safely in parallel.
- Prevent two workers from rendering the same clip.
- Add leases and recovery for abandoned jobs.

### 3.5 Storage is not durable enough

Pod-local storage is vulnerable to Pod replacement and capacity changes.

Required changes:

- Use a Runpod Network Volume or object storage for active and durable data.
- Keep only temporary scratch files on the GPU Pod disk.
- Store raw footage, proxies, transcripts, moments, EDLs, previews, and final clips separately.
- Add retention and cleanup rules.
- Add checksums and resumable uploads.
- Make job artifacts retrievable from any replacement worker.

### 3.6 GPU use is incomplete and not observable

Some analysis may use CUDA, while other work remains CPU-bound. Rendering may run on CPU fallback without making that clear.

Required changes:

- Load Whisper once per worker process.
- Use faster-whisper with CUDA on the GPU worker.
- Use NVENC for H.264 output.
- Measure GPU utilization and encoder selection.
- Track CPU time, GPU time, wall time, and encoder for each stage.
- Expose those metrics in the dashboard.

### 3.7 Error states are misleading

A failed render can eventually appear as “no clip passed QC,” even though the clip never successfully rendered.

Required changes:

- Distinguish `render_failed`, `qc_failed`, `ai_rejected`, `worker_unavailable`, `storage_failed`, `budget_exceeded`, and `cancelled`.
- Never run QC on a missing or incomplete output.
- Show the first root-cause error prominently.
- Preserve the full stage trace and retry history.

---

## 4. Target architecture

```text
Phone/browser
      ↓ HTTPS/private access
Remote dashboard/API
      ↓
Job service + PostgreSQL
      ↓
Redis/task queue
      ↓
 ┌────────────────┬──────────────────┬─────────────────┐
 │ Ingest worker  │ Analysis worker  │ Render worker   │
 │ yt-dlp/ffmpeg  │ CUDA Whisper     │ FFmpeg/NVENC    │
 │ storage        │ audio/CV/chat    │ templates/QC    │
 └────────────────┴──────────────────┴─────────────────┘
      ↓
Object storage / Network Volume
      ↓
Download-ready clips and raw footage
```

The Mac should normally be only a browser and optional development machine.

The preferred future deployment is:

```text
Remote host:
  dashboard/API
  PostgreSQL
  Redis
  n8n
  GPU worker
  Network Volume/object storage access
```

During the testing phase, it is acceptable for the dashboard and n8n to remain on the Mac while the GPU worker is remote. This should be treated as a transitional architecture.

---

## 5. Canonical job lifecycle

Use explicit, durable states:

```text
created
queued
downloading
downloaded
analyzing
analyzed
ranking
selected
planning
assets
rendering
rendered
qc
ready_for_review
approved
failed
cancelled
```

Every stage must store:

- Start time
- End time
- Attempt number
- Worker ID
- GPU/CPU metrics
- Input artifact IDs
- Output artifact IDs
- Error details
- Retryability

---

## 6. Remote ingestion plan

### File upload

- Upload directly to remote storage or the remote API.
- Do not stream large files through a Mac proxy as the long-term design.
- Show upload progress and resumable retry.
- Verify file checksum after upload.
- Create the job only after the artifact is confirmed remotely.

### YouTube

- Use yt-dlp only for content the user owns or is authorized to edit.
- Download directly on the remote worker.
- Store the original or best available source in durable storage.
- Extract metadata before analysis.

### Twitch

- Support finished VOD links.
- Support monitored channels.
- Record live segments into rolling files.
- Close each segment cleanly before analysis.
- Preserve raw segments separately from edited clips.

### Long streams

- Never download and analyze an entire multi-hour stream at full resolution by default.
- Create low-resolution proxy/index media first.
- Analyze audio, chat, and scene signals across the whole stream.
- Download or decode high-resolution windows only around selected candidates.

---

## 7. Faster analysis design

### Pass 1: inexpensive signal extraction

Run on the remote GPU worker or optimized CPU path:

- Whisper transcript and word timestamps
- Audio energy and scream-band energy
- Loudness changes
- Silence and dead-air detection
- Scene changes
- Facecam presence and coordinates
- Chat velocity and emote spikes
- Game/event keywords

### Pass 2: AI ranking

Send only the best candidate windows to the AI model:

- Transcript slice
- Low-resolution proxy
- Signal scores
- Candidate start/end
- Context before and after the peak

The AI should return strict JSON with:

- Start and end
- Event summary
- Why it is engaging
- Hook
- Confidence
- Quality score
- Recommended clip type
- Recommended duration

Cache all analysis artifacts by source checksum and configuration hash.

---

## 8. Faster rendering design

### Production renderer

Use one FFmpeg/NVENC pipeline for the standard layouts.

Required behavior:

- Normalize input FPS and time base.
- Normalize resolution before crop.
- Normalize audio sample rate and channel layout.
- Use filter-complex scripts stored as artifacts.
- Use `h264_nvenc` on Linux.
- Preserve original audio unless the user enables mixing.
- Use GPU scaling where supported.
- Avoid unnecessary intermediate files.

### GTA 6 — Gameplay Focus

- 1080×1920 output
- Gameplay fills the background
- Smart crop with safe fallback
- Facecam upper-left with rounded corners
- Optional transparent logo upper-right
- Captions off by default
- No automatic sound effects or unnecessary zooms
- User-adjustable crop, facecam size, facecam position, logo, and captions

### Render validation

Before accepting a render, verify:

- Correct dimensions
- Correct frame rate
- Video and audio duration match
- File decodes end to end
- NVENC was actually selected
- No black frame or crop failure
- Facecam is visible when expected
- Logo is present only when enabled
- Captions stay inside the safe area

### Fallback policy

Do not silently fall back from GPU rendering.

If compatibility fallback is retained:

- Label it `CPU fallback`.
- Record the exact reason.
- Show it in the dashboard.
- Allow the user to retry after a template fix.
- Do not send a misleading QC failure.

---

## 9. Queue and orchestration redesign

The worker service should own the pipeline.

Recommended queues:

```text
ingest
analysis
ranking
planning
assets
render
qc
cleanup
```

Each task must be:

- Idempotent
- Retryable
- Timeout-bound
- Observable
- Resumable
- Safe after worker restart

n8n should be limited to:

- Telegram notifications
- Scheduled Twitch/YouTube checks
- Human approval actions
- Optional publishing integrations
- Non-critical automations

---

## 10. Storage and artifact architecture

Recommended artifact paths:

```text
sources/{job_id}/original.mp4
sources/{job_id}/raw-segment-001.ts
proxies/{job_id}/index.mp4
analysis/{job_id}/metadata.json
analysis/{job_id}/transcript.json
analysis/{job_id}/signals.json
analysis/{job_id}/moments.json
edl/{job_id}/clip-00.json
renders/{job_id}/clip-00.mp4
renders/{job_id}/clip-00-preview.jpg
raw/{job_id}/original.mp4
```

Use:

- Network Volume for active processing
- Object storage for durable source and result files
- Signed URLs for downloads
- Automatic cleanup after the retention period
- Separate raw-footage retention from edited-clip retention

---

## 11. Dashboard improvements

The dashboard must show:

- Current stage
- Current sub-stage
- Queue position
- Elapsed time
- Estimated remaining time
- Worker name
- GPU type
- GPU utilization
- Render engine
- Encoder
- CPU fallback warning
- Disk usage
- Raw-footage availability
- Retry and cancel controls
- Exact failure reason

For each clip, display:

- Analysis time
- Edit-plan time
- Render time
- QC time
- Total time
- GPU/CPU engine
- Output resolution
- File size
- QC result

---

## 12. Reliability and recovery

Implement:

- Worker startup/readiness checks
- Automatic worker restart
- Job leases
- Stale-job detection
- Stage-level retry limits
- Explicit timeout handling
- Artifact checksum validation
- Database migrations
- Health checks for PostgreSQL, Redis, storage, FFmpeg, CUDA, and n8n
- Remote logs accessible from the dashboard
- Safe Pod stop and restart controls

The system must recover cleanly if:

- The Pod is stopped
- The Pod is replaced
- n8n restarts
- The dashboard restarts
- A render process crashes
- A network transfer is interrupted
- The AI provider returns an error
- The GPU runs out of memory

---

## 13. Cost controls

The testing system must remain safe with a small Runpod balance.

Implement:

- One-hour automatic Pod stop for testing
- Stop when the queue is empty and the idle timeout expires
- Per-job time limit
- Per-stage time limit
- Per-day AI budget
- Per-job AI budget
- Disk usage warning at 70%, 85%, and 95%
- Automatic cleanup of expired artifacts
- Dashboard display of current GPU hourly rate
- Dashboard display of estimated job cost

Never leave a test Pod running indefinitely.

---

## 14. Security

Implement:

- Private dashboard access through Tailscale or Cloudflare Access
- HTTPS only
- Worker token stored server-side
- No worker token exposed in browser JavaScript
- Signed, expiring download URLs
- Separate service credentials
- No secrets committed to GitHub
- Restricted Runpod ports
- Rate limits on upload and job creation
- Rights confirmation for downloaded content
- Audit log for source, user action, and artifact creation

---

## 15. Testing strategy

### Unit tests

- Job state transitions
- Idempotency
- Retry behavior
- EDL validation
- Crop calculations
- Facecam placement
- Caption bounds
- QC rules
- Budget enforcement

### Render fixtures

Test all of these:

1. Gameplay-only 16:9 video
2. Gameplay plus one facecam
3. Gameplay plus two people
4. No facecam
5. GTA logo enabled
6. GTA logo disabled
7. Captions enabled
8. Captions disabled
9. Hard crop scene
10. Scene that requires full-frame fallback
11. Audio with silence
12. Audio with music and speech

### Failure tests

- Missing `npx`
- Missing FFmpeg
- Missing NVENC
- Missing database
- Missing storage
- Pod restart during analysis
- Pod restart during rendering
- Network interruption during upload
- AI timeout
- Invalid EDL
- Corrupted source file

### End-to-end acceptance test

A short owned gaming video must produce:

- At least one downloadable MP4
- Correct 1080×1920 dimensions
- GPU encoder confirmation
- Passing QC
- A complete stage timeline
- A downloadable preview
- A raw-footage artifact when enabled

Record actual timings for every stage.

---

## 16. Recommended implementation order

### Phase 1 — Remote worker reliability

- Canonical startup script
- Environment loading
- Supervisor/restart policy
- Health and readiness checks
- Node/npm/npx validation
- CUDA/NVENC validation
- Clear worker errors

### Phase 2 — GPU rendering

- Fix GTA filter graph
- Add render fixtures
- Make NVENC the production path
- Remove silent fallback
- Add encoder telemetry
- Verify output and QC

### Phase 3 — Remote job engine

- Move orchestration from n8n into the worker service
- Add durable queue
- Add leases and retries
- Add cancellation and resume
- Preserve n8n for notifications and integrations

### Phase 4 — Durable storage

- Network Volume/object storage
- Raw footage storage
- Artifact metadata
- Signed downloads
- Cleanup and retention

### Phase 5 — Remote dashboard

- Deploy dashboard/API remotely
- Move n8n and PostgreSQL remotely
- Use private access
- Make the Mac UI-only

### Phase 6 — Advanced detection

- Twitch chat spikes
- Audio scream detection
- Multi-person tracking
- Better story assembly
- Stream segment indexing
- Batch jobs

### Phase 7 — Social workflow

- Caption recommendations
- Five hashtags
- TikTok private/draft upload
- Multiple TikTok and Instagram accounts
- Manual approval before publishing

---

## 17. Definition of done

The architecture is complete when:

- The Mac is not required for processing.
- A phone can create and monitor jobs.
- Files upload directly to remote storage.
- YouTube and Twitch ingestion happen remotely.
- Analysis happens remotely with CUDA where supported.
- Rendering uses NVENC for supported templates.
- Remotion is not silently used as a slow fallback.
- Jobs resume after worker or Pod restart.
- Raw footage can be retained separately.
- QC accurately distinguishes render failures from quality failures.
- The dashboard reports real engine, GPU, timing, and cost information.
- A one-hour test safety stop is active.
- A complete test job produces a downloadable, QC-passed MP4.

## 18. Final instruction to Claude

Do not start by making isolated UI changes.

First perform the audit, write `AUDIT_GAPS.md`, inspect the live deployment, and produce an implementation checklist. Then execute the plan in dependency order, testing after each phase. If a requirement conflicts with the existing architecture, explain the conflict and implement the safest compatible migration path.

The final response must include the audit findings, missing items, changes, test evidence, timings, GPU evidence, cost safeguards, and remaining limitations.
