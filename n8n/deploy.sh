#!/bin/zsh
# Regenerate and (re)deploy all Gaming Clipper workflows into the local n8n container.
# Reads the Telegram chat id from the existing digest workflow; prints no secrets.
set -e
cd "$(dirname "$0")/.."
CHAT=$(docker exec n8n sh -c 'n8n export:workflow --id=ad2QqSawfMNhGTLv --output=/tmp/w.json >/dev/null 2>&1; node -e "const w=require(\"/tmp/w.json\");const a=Array.isArray(w)?w[0]:w;process.stdout.write(String(a.nodes.find(n=>/telegram/.test(n.type)).parameters.chatId))"; rm -f /tmp/w.json')
[ -n "$CHAT" ] || { echo "could not read Telegram chat id"; exit 1; }
python3 n8n/build_workflows.py "$CHAT" >/dev/null
# order matters: sub-workflows must be published before workflows that call them
for f in errors assets maintenance regen publish watch main; do
  id=$(python3 -c "import json;print(json.load(open('data/n8n_build/gaming-clipper-$f.workflow.json'))['id'])")
  docker exec n8n n8n unpublish:workflow --id=$id >/dev/null 2>&1 || true
  docker exec -i n8n sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json 2>&1 | tail -1; rm -f /tmp/wf.json' < data/n8n_build/gaming-clipper-$f.workflow.json
  docker exec n8n n8n publish:workflow --id=$id 2>&1 | grep -v 'running n8n applies' | tail -1
  echo "deployed $f ($id)"
done
