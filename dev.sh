#!/usr/bin/env bash
# Fast test loop on a fixed ~30 s clip. Run without arguments for help.
set -euo pipefail
cd "$(dirname "$0")/worker"
if [ "${1:-}" = "test" ]; then exec uv run pytest -q; fi
exec uv run python -m clipper.dev "$@"
