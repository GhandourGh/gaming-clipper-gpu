#!/usr/bin/env bash
set -euo pipefail

ROOT="${CLIPPER_ROOT:-/workspace/gaming-clipper-host}"
STATE="${CLIPPER_STATE:-/workspace/clipper-runtime}"
ENV_FILE="${CLIPPER_ENV_FILE:-/workspace/clipper.env}"
mkdir -p "$STATE"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${CLIPPER_TOKEN:?CLIPPER_TOKEN is required in $ENV_FILE}"
export CLIPPER_DB_PASSWORD="${CLIPPER_DB_PASSWORD:-gpu-test-password}"
export CLIPPER_DB_DSN="postgresql://clipper:${CLIPPER_DB_PASSWORD}@127.0.0.1:5435/clipper"
export CLIPPER_HOST=0.0.0.0
export PORT=8787
export PYTHONPATH="$ROOT/worker"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3-turbo}"
export STORY_WHISPER_MODEL="${STORY_WHISPER_MODEL:-base}"
export RENDER_CONCURRENCY="${RENDER_CONCURRENCY:-2}"

if ! command -v ffmpeg >/dev/null || ! command -v psql >/dev/null || ! ldconfig -p 2>/dev/null | grep -q 'libnspr4.so'; then
  apt-get update
  apt-get install -y --no-install-recommends \
    ffmpeg postgresql postgresql-client ca-certificates curl \
    libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2 libpango-1.0-0 libcairo2
  rm -rf /var/lib/apt/lists/*
fi

PG_BIN="$(dirname "$(find /usr/lib/postgresql -name pg_ctl -type f | sort -V | tail -1)")"
PG_DATA="$STATE/postgres"
PG_LOG="$STATE/postgres.log"
if [[ ! -s "$PG_DATA/PG_VERSION" ]]; then
  mkdir -p "$PG_DATA"
  chown -R postgres:postgres "$PG_DATA" "$STATE"
  runuser -u postgres -- "$PG_BIN/initdb" -D "$PG_DATA" --encoding=UTF8 --locale=C.UTF-8 --auth-local=trust --auth-host=scram-sha-256
fi
chown -R postgres:postgres "$PG_DATA"
if ! runuser -u postgres -- "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
  runuser -u postgres -- "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" -o "-p 5435 -h 127.0.0.1" start
fi

runuser -u postgres -- psql -p 5435 -d postgres -v ON_ERROR_STOP=1 \
  --set=db_password="$CLIPPER_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE clipper LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'clipper') \gexec
SELECT 'CREATE DATABASE clipper OWNER clipper'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'clipper') \gexec
ALTER ROLE clipper PASSWORD :'db_password';
SQL

VENV="$STATE/venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install \
  'fastapi>=0.115' 'uvicorn>=0.30' 'pydantic>=2.8' 'psycopg[binary,pool]>=3.2' \
  'python-dotenv>=1.0' 'python-multipart>=0.0.9' 'numpy>=1.26' \
  'opencv-python-headless>=4.10,<5' 'httpx>=0.27' 'yt-dlp[default]>=2026.10.26' \
  'faster-whisper>=1.1' 'transformers>=4.46,<6'

NODE_DIR="$STATE/node-v20"
if [[ ! -x "$NODE_DIR/bin/node" ]]; then
  curl -fsSL https://nodejs.org/dist/latest-v20.x/SHASUMS256.txt -o "$STATE/node-shasums.txt"
  NODE_FILE="$(awk '/linux-x64.tar.xz$/ {print $2; exit}' "$STATE/node-shasums.txt")"
  curl -fsSL "https://nodejs.org/dist/latest-v20.x/$NODE_FILE" -o "$STATE/$NODE_FILE"
  mkdir -p "$NODE_DIR"
  tar -xJf "$STATE/$NODE_FILE" -C "$NODE_DIR" --strip-components=1
fi
export PATH="$NODE_DIR/bin:$PATH"
if [[ ! -d "$ROOT/renderer/node_modules" ]]; then
  npm --prefix "$ROOT/renderer" ci --omit=dev
fi

PID_FILE="$STATE/worker.pid"
if [[ -s "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  CMD="$(tr '\0' ' ' < "/proc/$(cat "$PID_FILE")/cmdline" 2>/dev/null || true)"
  if [[ "$CMD" == *"uvicorn clipper.main:app"* ]]; then
    kill "$(cat "$PID_FILE")"
    for _ in {1..20}; do kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break; sleep 0.25; done
  fi
fi

nohup "$VENV/bin/python" -m uvicorn clipper.main:app --host 0.0.0.0 --port 8787 \
  >"$STATE/worker.log" 2>&1 &
echo $! > "$PID_FILE"
sleep 2
curl -fsS http://127.0.0.1:8787/health
