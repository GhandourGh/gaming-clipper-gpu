#!/usr/bin/env bash
# Installs/restores everything the GPU worker needs from the /workspace volume and (re)starts the supervised services:
#   postgres (127.0.0.1:5435) · worker (127.0.0.1:8788, not exposed) · dashboard (0.0.0.0:8787, the only exposed port)
# Idempotent: a second run only restarts the services. Called by pod-start.sh (boot) and pod-update.sh (admin update).
set -uo pipefail
ROOT="${CLIPPER_ROOT:-/workspace/gaming-clipper-host}"
STATE="${CLIPPER_STATE:-/workspace/clipper-runtime}"
ENV_FILE="${CLIPPER_ENV_FILE:-/workspace/clipper.env}"
mkdir -p "$STATE" "$STATE/apt" "$STATE/hf"
log() { echo "[$(date -u +%T)] $*"; }
T0=$(date +%s)
exec 9>/tmp/clipper-pod-run.lock  # one run at a time (boot, /api/setup and /admin/update can overlap)
flock -w 600 9 || { log "another pod-run did not finish in 10 min; giving up"; exit 1; }

# ---------- environment (loaded here, so every supervised process gets it) ----------
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
if [[ -z "${CLIPPER_TOKEN:-}" ]]; then  # brand-new Pod: generate a worker token; the owner can replace it via /api/setup
  CLIPPER_TOKEN="$(openssl rand -hex 24)"; touch "$ENV_FILE"; chmod 600 "$ENV_FILE"; echo "CLIPPER_TOKEN=$CLIPPER_TOKEN" >> "$ENV_FILE"
  log "generated a CLIPPER_TOKEN in $ENV_FILE"
fi
if [[ ! -f "$STATE/setup-done" ]]; then  # one-time setup code, shown in the Runpod console log (never in boot.log)
  [[ -s "$STATE/setup-code" ]] || { openssl rand -hex 5 > "$STATE/setup-code"; chmod 600 "$STATE/setup-code"; }
  echo "CLIPPER SETUP CODE: $(cat "$STATE/setup-code")  (use once with POST /api/setup)" > /proc/1/fd/1 2>/dev/null || true
fi
: "${CLIPPER_DB_PASSWORD:=clipper-$(echo "$CLIPPER_TOKEN" | sha256sum | cut -c1-24)}"
export CLIPPER_TOKEN CLIPPER_DB_PASSWORD CLIPPER_ROOT="$ROOT" CLIPPER_STATE="$STATE" CLIPPER_ENV_FILE="$ENV_FILE"
export CLIPPER_DB_DSN="postgresql://clipper:${CLIPPER_DB_PASSWORD}@127.0.0.1:5435/clipper"
export CLIPPER_PORT=8788 PYTHONPATH="$ROOT/worker" PYTHONUNBUFFERED=1
export CLIPPER_WORKER_URL="http://127.0.0.1:8788"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3-turbo}" STORY_WHISPER_MODEL="${STORY_WHISPER_MODEL:-base}"
export RENDER_CPU_FALLBACK="${RENDER_CPU_FALLBACK:-0}" N8N_HEALTH_URL=none
export HF_HOME="$STATE/hf" TOKENIZERS_PARALLELISM=false
export DASHBOARD_URL="${DASHBOARD_URL:-https://${RUNPOD_POD_ID:-localhost}-8787.proxy.runpod.net}"

# ---------- system packages (container disk is reset on every Pod stop; .deb files are cached on the volume) ----------
APT=(apt-get -o "dir::cache::archives=$STATE/apt" -y -qq --no-install-recommends)
need=()
command -v pg_ctlcluster >/dev/null || ls /usr/lib/postgresql/*/bin/pg_ctl >/dev/null 2>&1 || need+=(postgresql postgresql-client)
command -v xz >/dev/null || need+=(xz-utils)
command -v curl >/dev/null || need+=(curl ca-certificates)
command -v git >/dev/null || need+=(git)
command -v fc-list >/dev/null || need+=(fontconfig)
command -v python3 >/dev/null || need+=(python3 python3-venv)
python3 -c "import venv, ensurepip" 2>/dev/null || need+=(python3-venv)
if ((${#need[@]})); then
  log "apt install ${need[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && "${APT[@]}" install "${need[@]}" >/dev/null || log "WARN: apt install failed"
fi

# ---------- ffmpeg with NVENC + libass (pick the first build that really encodes on this GPU) ----------
nvenc_ok() { "$1" -hide_banner -loglevel error -f lavfi -i testsrc2=size=256x256:rate=30 -t 0.5 -c:v h264_nvenc -f null - 2>/dev/null \
             && "$1" -hide_banner -filters 2>/dev/null | grep -qE '^ +[A-Z.]+ +ass +'; }  # flags column is 2 or 3 chars depending on the ffmpeg version
FFDIR="$STATE/ffmpeg"
pick=""
for cand in "$FFDIR/current/bin/ffmpeg"; do [[ -x "$cand" ]] && nvenc_ok "$cand" && pick="$cand"; done
if [[ -z "$pick" ]]; then
  for build in ffmpeg-n8.1-latest-linux64-gpl-8.1 ffmpeg-n9.0-latest-linux64-gpl-9.0 ffmpeg-master-latest-linux64-gpl; do
    if [[ ! -x "$FFDIR/$build/bin/ffmpeg" ]]; then
      log "downloading $build"
      mkdir -p "$FFDIR" && curl -fsSL "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$build.tar.xz" -o "$FFDIR/$build.tar.xz" \
        && tar --no-same-owner -xJf "$FFDIR/$build.tar.xz" -C "$FFDIR" && rm -f "$FFDIR/$build.tar.xz"
    fi
    if [[ -x "$FFDIR/$build/bin/ffmpeg" ]] && nvenc_ok "$FFDIR/$build/bin/ffmpeg"; then
      ln -sfn "$FFDIR/$build" "$FFDIR/current"; pick="$FFDIR/current/bin/ffmpeg"; break
    fi
    log "WARN: $build cannot use NVENC on this driver"
  done
fi
if [[ -z "$pick" ]]; then
  command -v /usr/bin/ffmpeg >/dev/null || "${APT[@]}" install ffmpeg >/dev/null 2>&1
  if nvenc_ok /usr/bin/ffmpeg; then mkdir -p "$FFDIR/system/bin"; ln -sf /usr/bin/ffmpeg "$FFDIR/system/bin/ffmpeg"; ln -sf /usr/bin/ffprobe "$FFDIR/system/bin/ffprobe"; ln -sfn "$FFDIR/system" "$FFDIR/current"; pick="$FFDIR/current/bin/ffmpeg"; fi
fi
[[ -n "$pick" ]] && log "ffmpeg: $(readlink -f "$pick") (NVENC ok)" || log "ERROR: no ffmpeg build can encode with NVENC here; /ready will report it"
export PATH="$FFDIR/current/bin:$PATH" FFMPEG_FULL="$FFDIR/current/bin/ffmpeg"

# ---------- PostgreSQL ----------
# Postgres must own its data directory. Some Runpod volumes refuse chown; then the data lives on the container disk and
# is dumped to the volume every 5 minutes and before an auto-stop (scripts/db-backup.sh), and restored on boot.
PG_BIN="$(dirname "$(ls /usr/lib/postgresql/*/bin/pg_ctl 2>/dev/null | sort -V | tail -1)")"
mkdir -p "$STATE/db"
touch "$STATE/db/.chown-test" && chown postgres "$STATE/db/.chown-test" 2>/dev/null && VOLUME_PG=1 || VOLUME_PG=0
rm -f "$STATE/db/.chown-test"
if [[ "$VOLUME_PG" == 1 ]]; then PG_DATA="$STATE/postgres"; PG_LOG="$STATE/postgres.log"
else PG_DATA="/var/lib/clipper-pg/data"; PG_LOG="/var/lib/clipper-pg/postgres.log"; ln -sfn "$PG_LOG" "$STATE/postgres.log" 2>/dev/null; fi
export CLIPPER_DB_BACKUP=$((1 - VOLUME_PG)) CLIPPER_DB_DUMP="$STATE/db/clipper.dump"
PSQL=(runuser -u postgres -- psql -q -p 5435 -d postgres -v ON_ERROR_STOP=1)  # unix socket: peer/trust auth for postgres
if [[ -x "$PG_BIN/pg_ctl" ]]; then
  fresh=0
  if [[ ! -s "$PG_DATA/PG_VERSION" ]]; then
    mkdir -p "$PG_DATA"; chown -R postgres:postgres "$(dirname "$PG_DATA")"; chmod 700 "$PG_DATA"
    runuser -u postgres -- "$PG_BIN/initdb" -D "$PG_DATA" --encoding=UTF8 --locale=C.UTF-8 --auth-local=trust --auth-host=scram-sha-256 >/dev/null
    fresh=1
  fi
  chown -R postgres:postgres "$PG_DATA"; chmod 700 "$PG_DATA"; touch "$PG_LOG"; chown postgres "$PG_LOG"
  if ! runuser -u postgres -- "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
    rm -f "$PG_DATA/postmaster.pid"  # stale lock left by a stopped Pod (never removed while Postgres runs)
    runuser -u postgres -- "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" -o "-p 5435 -h 127.0.0.1" -w start >/dev/null
  fi
  "${PSQL[@]}" --set=pw="$CLIPPER_DB_PASSWORD" <<'SQL' >/dev/null
SELECT format('CREATE ROLE clipper LOGIN PASSWORD %L', :'pw') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='clipper') \gexec
SELECT 'CREATE DATABASE clipper OWNER clipper' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='clipper') \gexec
ALTER ROLE clipper PASSWORD :'pw';
SQL
  if [[ "$fresh" == 1 && "$VOLUME_PG" == 0 && -s "$CLIPPER_DB_DUMP" ]]; then
    runuser -u postgres -- "$PG_BIN/pg_restore" -p 5435 -d clipper --no-owner --role=clipper "$CLIPPER_DB_DUMP" 2>/dev/null
    log "restored database from $CLIPPER_DB_DUMP"
  fi
  if runuser -u postgres -- psql -p 5435 -d clipper -tAc "select 1" >/dev/null 2>&1; then
    log "postgres up ($PG_BIN, data in $PG_DATA)"
  else log "ERROR: postgres did not start (see $PG_LOG)"; fi
else
  log "ERROR: PostgreSQL binaries missing"
fi

# ---------- Python venv (on the volume; reinstalled only when the dependency list changes) ----------
VENV="$STATE/venv"
[[ -x "$VENV/bin/python" ]] || python3 -m venv --system-site-packages "$VENV"
REQS='fastapi>=0.115 uvicorn>=0.30 pydantic>=2.8 psycopg[binary,pool]>=3.2 python-dotenv>=1.0 python-multipart>=0.0.9
numpy>=1.26 opencv-python-headless>=4.10,<5 httpx>=0.27 yt-dlp[default]>=2026.10.26 faster-whisper>=1.1 transformers>=4.46
nvidia-cublas-cu12 nvidia-cudnn-cu12==9.* av>=14'
STAMP="$STATE/venv.reqs"
if [[ "$(cat "$STAMP" 2>/dev/null)" != "$REQS" ]]; then
  log "pip install"
  "$VENV/bin/pip" install -q --upgrade pip && "$VENV/bin/pip" install -q $REQS && echo "$REQS" > "$STAMP" || log "WARN: pip install failed"
fi
"$VENV/bin/python" -c "import torch" 2>/dev/null || { log "pip install torch"; "$VENV/bin/pip" install -q torch torchaudio || log "WARN: torch install failed"; }
"$VENV/bin/pip" install -q -U "yt-dlp[default]" >/dev/null 2>&1 &  # sites change often; keep yt-dlp fresh
# CUDA 12 cuBLAS/cuDNN for faster-whisper (CTranslate2) come from pip wheels; nvidia/* are namespace packages (no __file__)
NV_LIBS="$(find "$VENV" -type d \( -path '*/nvidia/cublas/lib' -o -path '*/nvidia/cudnn/lib' -o -path '*/nvidia/cuda_runtime/lib' \) 2>/dev/null | paste -sd: -)"
log "CUDA libs for Whisper: ${NV_LIBS:-none found}"
export LD_LIBRARY_PATH="${NV_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# ---------- Node + dashboard build (on the volume; rebuilt only when the code changes) ----------
NODE_DIR="$STATE/node-v22"
if [[ ! -x "$NODE_DIR/bin/node" ]]; then
  log "installing Node 22"
  curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt -o "$STATE/node-shasums.txt"
  NODE_FILE="$(awk '/linux-x64.tar.xz$/ {print $2; exit}' "$STATE/node-shasums.txt")"
  curl -fsSL "https://nodejs.org/dist/latest-v22.x/$NODE_FILE" -o "$STATE/$NODE_FILE" \
    && (cd "$STATE" && grep " $NODE_FILE\$" node-shasums.txt | sha256sum -c - >/dev/null) \
    && mkdir -p "$NODE_DIR" && tar --no-same-owner -xJf "$STATE/$NODE_FILE" -C "$NODE_DIR" --strip-components=1 && rm -f "$STATE/$NODE_FILE"
fi
export PATH="$NODE_DIR/bin:$PATH"
REV="$(git -C "$ROOT" rev-parse HEAD:dashboard 2>/dev/null || echo none)"  # tree hash: rebuild only when the dashboard changed
LOCK_SUM="$(sha256sum "$ROOT/dashboard/package-lock.json" | cut -c1-16)"
if [[ "$(cat "$STATE/dashboard.lock" 2>/dev/null)" != "$LOCK_SUM" || ! -d "$ROOT/dashboard/node_modules" ]]; then
  log "npm ci (dashboard)"
  (cd "$ROOT/dashboard" && npm ci --no-audit --no-fund --loglevel=error) && echo "$LOCK_SUM" > "$STATE/dashboard.lock"
fi
if [[ "$(cat "$STATE/dashboard.rev" 2>/dev/null)" != "$REV" || ! -d "$ROOT/dashboard/.next" ]]; then
  log "next build"
  (cd "$ROOT/dashboard" && NEXT_TELEMETRY_DISABLED=1 npx next build >"$STATE/dashboard-build.log" 2>&1) \
    && echo "$REV" > "$STATE/dashboard.rev" || log "ERROR: dashboard build failed (see dashboard-build.log)"
fi

# ---------- supervised services ----------
stop_sup() {
  local f="$STATE/$1.sup.pid"
  if [[ -s "$f" ]]; then kill -TERM -- -"$(cat "$f")" 2>/dev/null; sleep 1; kill -KILL -- -"$(cat "$f")" 2>/dev/null; rm -f "$f"; fi
}
supervise() {  # supervise NAME CMD...: restart CMD 3 s after it exits, forever; own process group for clean stops
  local name="$1"; shift
  stop_sup "$name"
  setsid bash -c 'while true; do echo "== start $(date -u +%FT%TZ)"; "$@"; echo "== exited $? $(date -u +%FT%TZ)"; sleep 3; done' _ "$@" \
    >>"$STATE/$name.log" 2>&1 < /dev/null 9>&- &  # 9>&-: services must not inherit (and hold) the pod-run lock
  echo $! > "$STATE/$name.sup.pid"
}
pkill -f "uvicorn clipper.main:app" 2>/dev/null; pkill -f "next start" 2>/dev/null; pkill -f "next-server" 2>/dev/null; sleep 1
cd "$ROOT/worker"
supervise worker "$VENV/bin/python" -m uvicorn clipper.main:app --host 127.0.0.1 --port 8788 --log-level warning
if [[ "$CLIPPER_DB_BACKUP" == 1 ]]; then supervise dbbackup bash -c "while true; do sleep 300; bash '$ROOT/scripts/db-backup.sh'; done"; else stop_sup dbbackup; fi
cd "$ROOT/dashboard"
supervise dashboard env PORT=8787 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1 "$NODE_DIR/bin/npx" next start -H 0.0.0.0 -p 8787

for _ in $(seq 1 90); do curl -fsS -m 2 http://127.0.0.1:8788/health >/dev/null 2>&1 && break; sleep 1; done
log "worker: $(curl -fsS -m 5 http://127.0.0.1:8788/health 2>&1 | head -c 200)"
for _ in $(seq 1 60); do curl -fsS -m 2 -o /dev/null http://127.0.0.1:8787/login 2>/dev/null && break; sleep 1; done
log "dashboard: HTTP $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8787/login)"
log "ready: $(curl -fsS -m 90 'http://127.0.0.1:8788/ready?refresh=1' 2>&1 | head -c 400)"
log "pod-run finished in $(( $(date +%s) - T0 )) s"
