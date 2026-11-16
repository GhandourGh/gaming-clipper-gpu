#!/usr/bin/env bash
# Dump the clipper database to the /workspace volume (atomic replace). Used when Postgres data lives on the container disk.
set -uo pipefail
OUT="${CLIPPER_DB_DUMP:-/workspace/clipper-runtime/db/clipper.dump}"
PGD="$(ls /usr/lib/postgresql/*/bin/pg_dump 2>/dev/null | sort -V | tail -1)"
[[ -x "$PGD" ]] || exit 0
mkdir -p "$(dirname "$OUT")"
runuser -u postgres -- "$PGD" -p 5435 -Fc clipper > "$OUT.tmp" 2>/dev/null && [[ -s "$OUT.tmp" ]] && mv -f "$OUT.tmp" "$OUT" && echo "db backup $(date -u +%T) $(stat -c %s "$OUT") bytes"
