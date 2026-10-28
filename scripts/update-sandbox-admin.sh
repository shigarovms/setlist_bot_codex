#!/usr/bin/env bash
# RU: Обновление только тестового контура. EN: Never use the production compose file.
set -euo pipefail
cd -P "$(dirname "$0")/.."
if [[ "$PWD" != /opt/setlist_bot_sandbox ]]; then
  echo "Run only from /opt/setlist_bot_sandbox on the VPS."
  exit 1
fi
mode="${1:?Usage: bash scripts/update-sandbox-admin.sh server|cloud}"
if [[ "$mode" != server && "$mode" != cloud ]]; then
  echo "Choose server or cloud."
  exit 1
fi
python3 - <<'PY'
from pathlib import Path
values = {}
for line in Path('.env').read_text().splitlines():
    name, sep, value = line.partition('=')
    if sep:
        values[name.strip()] = value.strip()
if values.get('SANDBOX_BOT_TOKEN', '').split(':', 1)[0] != '1110437563':
    raise SystemExit('Unexpected sandbox bot token identity; stopping.')
PY
compose=(docker compose -f compose.sandbox.yaml -f compose.admin.sandbox.yaml)
"${compose[@]}" config --quiet
# All three services use the same explicitly named image.
"${compose[@]}" build --build-arg "APP_REVISION=$(git rev-parse HEAD)" bot
"${compose[@]}" stop bot admin
SETLIST_SANDBOX_DIR="$PWD" bash scripts/backup-sandbox.sh
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps --entrypoint .venv/bin/setlist-bot migrate --check
"${compose[@]}" up -d --no-build --no-deps admin
if [[ "$mode" == server ]]; then
  "${compose[@]}" up -d --no-build --no-deps bot
else
  echo "VPS polling stopped. Start cloud polling only after tunnel/database checks."
fi
"${compose[@]}" ps
"${compose[@]}" logs --tail 20 bot admin
