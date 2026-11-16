#!/usr/bin/env bash
# Runpod Pod start hook. Set the Pod's "Container Start Command" to:
#   bash -c "bash /workspace/gaming-clipper-host/scripts/pod-start.sh & exec /start.sh"
# (the template's own /start.sh keeps Jupyter/SSH working). Safe to run by hand any time.
# 1) brings the code on the volume up to date from GitHub, 2) hands over to pod-run.sh from that fresh checkout.
set -uo pipefail
ROOT="${CLIPPER_ROOT:-/workspace/gaming-clipper-host}"
STATE="${CLIPPER_STATE:-/workspace/clipper-runtime}"
REPO="${CLIPPER_REPO:-https://github.com/GhandourGh/gaming-clipper-gpu.git}"
BRANCH="${CLIPPER_BRANCH:-master}"
mkdir -p "$STATE"
exec >>"$STATE/boot.log" 2>&1
echo "== pod-start $(date -u +%FT%TZ) pod=${RUNPOD_POD_ID:-?}"

command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
if [[ -d "$ROOT/.git" ]]; then
  if [[ "${CLIPPER_AUTO_UPDATE:-1}" == "1" ]]; then
    git -C "$ROOT" fetch -q origin "$BRANCH" && git -C "$ROOT" reset -q --hard "origin/$BRANCH" \
      || echo "WARN: could not update code; starting the version on disk"
  fi
else
  if [[ -e "$ROOT" ]]; then  # an older copy uploaded without git: keep its data/, replace the code
    mv "$ROOT" "$ROOT.pre-git.$(date +%s)"
  fi
  git clone -q --branch "$BRANCH" "$REPO" "$ROOT" || { echo "FATAL: git clone failed"; exit 1; }
  OLD="$(ls -d "$ROOT".pre-git.* 2>/dev/null | tail -1)"
  if [[ -n "$OLD" && -d "$OLD/data" && ! -e "$ROOT/data" ]]; then mv "$OLD/data" "$ROOT/data"; fi
  if [[ -n "$OLD" && -d "$OLD/inbox" ]]; then mkdir -p "$ROOT/inbox"; cp -an "$OLD/inbox/." "$ROOT/inbox/" 2>/dev/null || true; fi
fi
echo "code at $(git -C "$ROOT" rev-parse --short HEAD)"
exec bash "$ROOT/scripts/pod-run.sh"
