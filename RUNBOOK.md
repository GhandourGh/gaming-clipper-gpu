# Gaming Clipper on the GPU — Runbook

Everything (download, Whisper, moment finding, AI planning, NVENC rendering, QC, storage, dashboard) runs on the Runpod
Pod. The Mac is only a browser. n8n is not needed by the pipeline.

## One-time setup on a Pod

1. **Start (or create) the Pod** in the Runpod console. Image `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
   (or any CUDA 12 image with `/start.sh`), container disk ≥ 20 GB, volume ≥ 50 GB at `/workspace`,
   **HTTP port 8787 exposed** (Jupyter 8888 optional).
2. **Bootstrap once** — open the Pod's Jupyter → Terminal (or Web Terminal) and run:
   ```bash
   cd /workspace && ( [ -d gaming-clipper-host/.git ] || git clone https://github.com/GhandourGh/gaming-clipper-gpu.git gaming-clipper-host.new ) ; \
   [ -d gaming-clipper-host.new ] && { [ -e gaming-clipper-host ] && mv gaming-clipper-host gaming-clipper-host.old; mv gaming-clipper-host.new gaming-clipper-host; \
     [ -d gaming-clipper-host.old/data ] && mv gaming-clipper-host.old/data gaming-clipper-host/; } ; \
   grep -q '^CLIPPER_TOKEN=' /workspace/clipper.env 2>/dev/null || echo "CLIPPER_TOKEN=$(openssl rand -hex 24)" >> /workspace/clipper.env ; \
   bash /workspace/gaming-clipper-host/scripts/pod-start.sh ; tail -n 30 /workspace/clipper-runtime/boot.log
   ```
   First run takes ~5–8 min (ffmpeg, Postgres, Python, Node, dashboard build, Whisper download). Later boots: ~1–2 min.
3. **Make it automatic** — Pod ⋮ → Edit Pod → *Container Start Command*:
   ```
   bash -c "bash /workspace/gaming-clipper-host/scripts/pod-start.sh & exec /start.sh"
   ```
   From then on every Pod start pulls the latest code and starts everything by itself.
4. **Open the dashboard**: `https://<POD_ID>-8787.proxy.runpod.net` → sign in with the access key
   (`CLIPPER_TOKEN` from `/workspace/clipper.env`, or set `DASHBOARD_PASSWORD` there).
5. **GPU page → Keys on the Pod**: paste the Gemini API key (required), optionally Telegram bot token + chat id,
   OpenRouter key, and the Pod's hourly price (for the cost display). Then **Re-run checks**: every row must be OK.

## Daily use
- Start the Pod in the Runpod console (phone works), open the dashboard URL, create a job.
- The Pod **stops itself** after 30 idle minutes (no jobs, nobody on the dashboard, no live channel enabled).
  Change with `IDLE_STOP_MIN` (GPU page → keys form, or `/workspace/clipper.env`). Stopping keeps `/workspace`.
- **GPU page**: live GPU load, VRAM, encoder sessions, NVENC proof, Whisper device, disk, uptime, session cost,
  *Update code* (pulls GitHub `master`, rebuilds, restarts), *Stop Pod now*.

## Where things are on the Pod
| What | Where |
|---|---|
| Code | `/workspace/gaming-clipper-host` (git, `master`) |
| Settings/keys | `/workspace/clipper.env` (mode 600) |
| Jobs, clips, raw footage | `/workspace/gaming-clipper-host/data/jobs/<job_id>/` |
| Postgres data | `/workspace/clipper-runtime/postgres` (127.0.0.1:5435) |
| Logs | `/workspace/clipper-runtime/{boot,worker,dashboard,postgres,update}.log` (also GET `/admin/logs?name=`) |
| ffmpeg (NVENC + libass) | `/workspace/clipper-runtime/ffmpeg/current/bin` |

## Troubleshooting
- Dashboard 502 right after start: still booting — `tail -f /workspace/clipper-runtime/boot.log`.
- GPU page "FFmpeg + NVENC: Problem": the driver is older than the ffmpeg build; `pod-run.sh` tries 7.1, 6.1, then the
  distro ffmpeg — see `boot.log`.
- A job shows **Root cause** at the top of its page; *Retry failed* reuses finished stages; *Cancel* stops queued work.
- "Pod's GPUs are no longer available" when starting: the host's GPU was rented out while stopped. Wait and retry,
  or use Runpod's *migrate* (new Pod ID → new dashboard URL; data is copied).

## Mac (optional clean-up once the Pod works)
- Unpublish the old n8n clipper workflows (they would call a stopped Pod):
  `for id in gclipMainFlow001 gclipRegenFlow01 gclipAssetsFlw01 gclipWatchFlow01 gclipMaintFlow01 gclipPublishFl01 gclipErrorsFlw01; do docker exec n8n n8n unpublish:workflow --id=$id; done`
- Stop the Mac worker/dashboard launch agents if you no longer want the local copy.
