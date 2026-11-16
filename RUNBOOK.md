# Gaming Clipper on the GPU — Runbook

Everything (download, Whisper, moment finding, AI planning, NVENC rendering, QC, storage, dashboard) runs on the Runpod
Pod. The Mac is only a browser. n8n is not needed by the pipeline.

## One-time setup on a new Pod (no terminal needed)

1. **Deploy** (Runpod console → Deploy): any CUDA 12/13 PyTorch template (current Pod: `runpod/pytorch:2.1.0-cu1320-torch280-ubuntu2404`),
   GPU with NVENC (RTX 2000 Ada $0.24/hr is enough), **Volume disk** ≥ 30 GB at `/workspace`,
   **Set overrides**: add HTTP port **8787**, Container start command:
   ```
   bash -c "if [ -f /workspace/gaming-clipper-host/scripts/pod-start.sh ]; then bash /workspace/gaming-clipper-host/scripts/pod-start.sh & else curl -fsSL https://raw.githubusercontent.com/GhandourGh/gaming-clipper-gpu/master/scripts/pod-start.sh | bash & fi; exec /start.sh"
   ```
2. First boot clones the code, installs ffmpeg (NVENC), Postgres, Python, Node, builds the dashboard (~5–10 min) and prints
   `CLIPPER SETUP CODE: …` in the Pod's **Logs** tab.
3. **Claim it once** from the Mac (keys go straight to `/workspace/clipper.env`, never through a web form):
   ```bash
   curl -X POST https://<POD_ID>-8787.proxy.runpod.net/api/setup -H 'content-type: application/json' \
     -d '{"code":"<SETUP CODE>","values":{"CLIPPER_TOKEN":"<your token>","GEMINI_API_KEY":"<key>","GPU_USD_PER_HOUR":"0.25"}}'
   ```
   The services restart with the new settings; the code then stops working (410).
4. **Open the dashboard**: `https://<POD_ID>-8787.proxy.runpod.net` → access key = `CLIPPER_TOKEN`
   (or `DASHBOARD_PASSWORD` if set). GPU page → every check OK.

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
