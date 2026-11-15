#!/bin/zsh
set -a
source "$(dirname "$0")/../.env"
set +a
cd "$(dirname "$0")"
exec npm run dev
