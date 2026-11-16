#!/usr/bin/env bash
# Started by POST /admin/update (detached). Pulls the latest code and re-runs pod-run.sh, which rebuilds only what
# changed and restarts the supervised worker + dashboard. Jobs in progress resume from Postgres after the restart.
set -uo pipefail
ROOT="${CLIPPER_ROOT:-/workspace/gaming-clipper-host}"
echo "== pod-update $(date -u +%FT%TZ)"
git -C "$ROOT" fetch -q origin "${CLIPPER_BRANCH:-master}" && git -C "$ROOT" reset -q --hard "origin/${CLIPPER_BRANCH:-master}" || { echo "git update failed"; exit 1; }
echo "code at $(git -C "$ROOT" rev-parse --short HEAD)"
exec bash "$ROOT/scripts/pod-run.sh"
