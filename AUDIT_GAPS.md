# Remote GPU Clipper — Audit Gaps (revision 2)

Audit date: 2026-11-16. Labels: **[C]** confirmed by code, a command, or the Runpod console · **[I]** inferred · **[R]** needs a runtime test on a running Pod.

## 0. Corrected premise: two checkouts, and the GPU work is in the other one

Revision 1 of this file said "no Runpod/NVENC code exists". **That was wrong.** I only searched `~/Desktop/ai-automation-agency/gaming-clipper` (branch `feat/gaming-clipper`, git history of 13 commits on `main`/`feat`). The deployed system is a **separate git repo**:

| | Desktop checkout (what I first audited) | Hosted checkout `~/Applications/gaming-clipper-host` |
|---|---|---|
| Branch / history | `feat/gaming-clipper`, `main` | `master` (+ `origin/master`), 11 GPU commits dated 2026-11-16 |
| Runs where | nothing running from it | **dashboard :3001 and local worker :8787 are served from here** **[C]** (process cwd) |
| GPU work | none (VideoToolbox, mlx-whisper) | NVENC, faster-whisper CUDA, Linux bootstrap, Dockerfile.gpu **[C]** |
| Uncommitted | 18 modified files | `dashboard/tsconfig.tsbuildinfo`, `n8n/build_workflows.py`, untracked `REMOTE_GPU_ARCHITECTURE_PLAN.md` |

The two histories are unrelated. The Desktop branch has the newer gaming-clipper features (gameplay crop, story QC, live recorder changes). The hosted master has the GPU port. **Any further work needs a decision on which one is the base, and a merge plan.** Editing the Desktop copy would not change what is deployed.

`REMOTE_GPU_ARCHITECTURE_PLAN.md` in the hosted repo is the same spec you pasted.

### What the hosted checkout already has **[C]**
- `scripts/bootstrap-runpod.sh`: loads `/workspace/clipper.env`, installs apt packages, runs Postgres inside the Pod on :5435 (data under `/workspace/clipper-runtime`), creates a venv with `faster-whisper`, installs Node 20, starts uvicorn with `nohup`. It is **idempotent but not a supervisor**: there is no restart-on-crash, and it starts the worker in the background and exits. `FFMPEG_FULL=/usr/bin/ffmpeg` (distro ffmpeg).
- `fastrender.py:489`: `h264_nvenc` (`-preset p4 -cq 19`) on Linux, else VideoToolbox. Failure raises `RuntimeError("fast render failed …")`.
- `analysis.py:68-76`: faster-whisper `device="cuda", float16`, with a CPU `int8` fallback.
- `worker/Dockerfile.gpu` exists (11-line change).
- `dashboard/app/gpu-test` page + `components/gpu-test.tsx` (28 lines): static info panel. It says "Run GPU clipping test" can't run yet.

### Still true from revision 1 **[C]** (code is shared between both checkouts)
- Render fallback: `render.py` still catches a fast-render failure and silently reruns Remotion. The engine is only in the stage result, not on the clip row, and nothing records the encoder or GPU.
- Queue: `queue.py` is one in-process thread. A restart re-queues `queued`/`running` stages (good), but there is no worker id, lease or cancel.
- n8n owns orchestration: `Wait analysis` (20 s) and `Wait render` (15 s) loops, **and** the Gemini rank/director/review/moderation calls.
- Uploads go browser → Mac dashboard → worker. No object storage, no checksums, no artifact table.
- Schema is `create if not exists`, with no migrations.

## 1. Live Runpod state (console, signed in 2026-11-16) **[C]**

| Pod | ID | GPU | State now |
|---|---|---|---|
| gaming-clipper-gpu-test | `rfbevtshgimov3` | RTX A4000 | **Stopped** (was already stopped when checked) |
| gaming-clipper-gpu-test-a5000 | `8ugvh4zuuzzwlk` | RTX A5000 | **Stopped by me** at your request (was running, ~0% GPU/CPU) |

Both now bill idle disk only (~$0.01/hr; the A5000 stop dialog quoted $0.014/hr). The Runpod MCP is **not** connected (OAuth did not complete); the console in the browser pane works.

### Consequences of the A5000 being stopped
- **n8n workflows are hard-wired to the A5000.** `n8n/build_workflows.py:10` has `WORKER = "https://8ugvh4zuuzzwlk-8787.proxy.runpod.net"` (uncommitted edit; the committed value is the A4000's proxy URL). The URL is baked into the *generated* workflows, so a Pod change means regenerating and redeploying all n8n workflows (`n8n/deploy.sh`). With the Pod stopped, any n8n-driven job will fail at "Create job". **[C]**
- The dashboard's GPU test page still shows the A4000, and its "Open Runpod Pod" link returns a 404 (`/pods/<id>` is not a valid console route). **[C]**
- The local worker on :8787 is **not** the GPU worker. It runs the hosted checkout on the M1, so the dashboard's `CLIPPER_WORKER_URL` decides which one a job reaches. The value is set in the hosted `.env`; I did not print it. **[C]**
- Starting a stopped Pod may land on different hardware: the stop dialog says availability depends on the machine, and **proxy URLs are tied to the Pod ID**, which is why the A4000 → A5000 switch changed the URL. **[C]**

## 2. Gaps and risks (updated)

1. **Two divergent repos** (section 0). Highest risk. Decide the base before any code change.
2. **Worker URL is hard-coded into n8n** and changes with every new Pod. Needs a config value (env or n8n variable), not a build-time constant. **[C]**
3. **No supervisor** (spec 3.2). `bootstrap-runpod.sh` backgrounds uvicorn and Postgres with no restart policy, and a Pod restart needs the script run again. **[C]**
4. **`/health` only**, with no `/ready` preflight for ffmpeg/NVENC/CUDA/node. **[C]**
5. **NVENC availability on distro ffmpeg is unverified.** `/usr/bin/ffmpeg` from apt may lack NVENC or libass. The Pod must be started to test (`ffmpeg -encoders | grep nvenc`). **[R]**
6. **libass captions and `drawtext` run on CPU.** Only encode (and optionally decode/scale) is GPU. **[I]**
7. **Silent Remotion fallback persists** and now also needs Chrome libs, which the bootstrap installs. Spec 3.1 asks for it to be labelled or removed. **[C]**
8. **Postgres inside the Pod** (`/workspace/clipper-runtime`) is durable across Stop only while the volume disk survives. It is not reachable by the Mac. The dashboard reads job state through the worker API, which is fine, but terminating the Pod loses all job history. **[C]**
9. **YouTube downloads from a datacenter IP** may be blocked or need cookies. **[I][R]**
10. **Gemini calls and keys sit in n8n.** Moving orchestration to the worker moves keys with it. **[C]**
11. **Pod cost safety:** the page says timed auto-stop is not available through the Pod API; I confirmed only that a manual stop works. Pods are not auto-stopping, and I found one running with no job on it. **[C]**
12. **Dashboard stale-build risk:** the running dashboard is a prebuilt `next-server` from the hosted checkout (`run-production.sh` rebuilds on login). **[C]**
13. **`.claude/launch.json` "dashboard" entry fails** (`./run.sh` not openable under zsh) in the Desktop checkout. **[C]**
14. **Rights confirmation, rate limits, audit log**: only session auth exists. **[I]**
15. **No render fixtures or GPU-marked tests** in either checkout. **[C]**

## 3. Dependency-ordered checklist (nothing implemented yet)

0. **Decide the base repo and merge plan** (hosted `master` vs Desktop `feat/gaming-clipper`). Commit or stash uncommitted work in both.
1. **Start one Pod (with approval)** and capture: `nvidia-smi`, `ffmpeg -encoders | grep nvenc`, `ffmpeg -filters | grep -E 'ass|drawtext'`, node/npx, `/health`. Fills items 5, 9 and the GPU-evidence rows. Stop it after.
2. **Worker reliability:** supervisor/restart, `/ready` preflight, one startup script that always loads `/workspace/clipper.env`, pinned Python and Node.
3. **GPU render:** record `engine/encoder/gpu` on the clip row, remove or label the Remotion fallback, add the render fixtures from spec section 15, test the GTA template on NVENC.
4. **Job engine:** leases, cancel/retry, split failure statuses, move n8n poll loops and Gemini calls into the worker, make the worker URL configurable.
5. **Storage, remote dashboard/DB, detection upgrades, social workflow**, in that order, after 1–4 are verified.

Cross-cutting: Pod cost guard (idle auto-stop from inside the Pod or a scheduled check), dashboard telemetry.

## 4. Open decisions for the owner
- Which checkout is the base for further work (see section 0)?
- Should I start a Pod for the Step 1 measurements? (Each start costs money; I will stop it afterwards and report the cost.)
- Do you want n8n regenerated to point at a configurable worker URL before any Pod is restarted?
