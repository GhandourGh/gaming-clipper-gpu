#!/bin/zsh
# Start the clipper worker on 127.0.0.1 (n8n reaches it via host.docker.internal).
export PATH="/Users/macos/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")"
docker compose -p gaming-clipper -f ../compose.yaml up -d >/dev/null
PORT=$(grep -E '^CLIPPER_PORT=' ../.env | cut -d= -f2)
exec .venv/bin/python -m uvicorn clipper.main:app --host "${CLIPPER_HOST:-127.0.0.1}" --port "${PORT:-8787}" --log-level warning
