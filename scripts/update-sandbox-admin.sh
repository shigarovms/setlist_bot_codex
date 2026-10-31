#!/usr/bin/env bash
# RU: Обновление только тестового контура. EN: Never use the production compose file.
set -euo pipefail
umask 077
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
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree must be clean; preserve local changes before releasing."
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
calendar_enabled=false
if [[ -f .env.calendar ]]; then
  compose+=(-f compose.calendar.sandbox.yaml)
  calendar_enabled=true
fi
production_access="$(python3 - <<'PY'
from pathlib import Path
values = {}
for line in Path('.env.admin').read_text().splitlines():
    name, sep, value = line.partition('=')
    if sep and not line.lstrip().startswith('#'):
        values[name.strip()] = value.strip().strip("\"'")
enabled = values.get('WEB_ADMIN_ALLOW_PRODUCTION', '').lower() in ('true', '1', 'yes', 'on')
configured = bool(values.get('WEB_ADMIN_PRODUCTION_DATABASE_URL', ''))
if enabled != configured:
    raise SystemExit('Incomplete production admin connection; preserve configuration and inspect it.')
print('true' if enabled else 'false')
PY
)"
if [[ "$production_access" == true ]]; then
  # RU: Сохраняем уже подключённую админку production, не меняя секреты и роль БД.
  # EN: Preserve the existing external network and metadata mount during recreation.
  compose+=(-f compose.admin.production-access.yaml)
fi
"${compose[@]}" config --quiet
# All three services use the same explicitly named image.
"${compose[@]}" build --build-arg "APP_REVISION=$(git rev-parse HEAD)" bot
"${compose[@]}" stop bot admin
if [[ "$calendar_enabled" == true ]]; then
  "${compose[@]}" stop calendar
fi
SETLIST_SANDBOX_DIR="$PWD" bash scripts/backup-sandbox.sh
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps --entrypoint .venv/bin/setlist-bot migrate --check
"${compose[@]}" up -d --no-build --no-deps admin
"${compose[@]}" exec -T admin .venv/bin/python - <<'PY'
import json
import time
from urllib.request import urlopen
for attempt in range(15):
    try:
        with urlopen('http://127.0.0.1:8080/sandbox/healthz', timeout=3) as response:
            assert json.load(response).get('status') == 'ok'
        print('Sandbox admin health OK.', flush=True)
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit('Sandbox admin is not healthy; inspect logs before starting polling.')
PY
if [[ "$calendar_enabled" == true ]]; then
  "${compose[@]}" up -d --no-build --no-deps calendar
  "${compose[@]}" up -d --no-build calendar_proxy
fi
if [[ "$mode" == server ]]; then
  "${compose[@]}" up -d --no-build --no-deps bot
else
  echo "VPS sandbox polling remains stopped. Cloud polling uses its own independent sandbox database."
fi
if [[ "$production_access" == true ]]; then
  echo "Production admin network preserved. Its section becomes available after the production schema is updated."
fi
"${compose[@]}" ps
"${compose[@]}" logs --tail 20 bot admin
