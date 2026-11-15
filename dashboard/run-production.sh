#!/bin/zsh
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
set -a
source "$(dirname "$0")/../.env"
set +a
cd "$(dirname "$0")"
# Build on every login so the hosted dashboard always matches the checked-out app.
npm run build
exec npm run start
