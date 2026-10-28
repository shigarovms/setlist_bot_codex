#!/usr/bin/env bash
# Prepare a separate sandbox and web UI. Deliberately never start a polling bot here.
set +x
set -euo pipefail
cd -P "$(dirname "$0")/.."
if [[ "$PWD" == /opt/setlist_bot_codex ]]; then
  printf 'Refusing to prepare a sandbox inside the production checkout.\n' >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  umask 077
  python3 - <<'PY'
import getpass, os, re
value=getpass.getpass('Test bot token (hidden, not the production token): ').strip()
if not re.fullmatch(r'1110437563:[A-Za-z0-9_-]{30,}',value):
    raise SystemExit('This setup accepts only @botToPract696_bot; production is forbidden.')
fd=os.open('.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as output:
    output.write('SANDBOX_BOT_TOKEN='+value+'\n')
PY
fi
python3 - <<'PY'
from pathlib import Path
values={}
for line in Path('.env').read_text().splitlines():
    key,sep,value=line.partition('=')
    if sep: values[key]=value.strip()
if values.get('SANDBOX_BOT_TOKEN','').split(':',1)[0] != '1110437563':
    raise SystemExit('Unexpected sandbox token identity; stopping.')
PY
docker compose -f compose.sandbox.yaml build bot migrate
docker compose -f compose.sandbox.yaml up -d --wait db
if docker compose -f compose.sandbox.yaml exec -T db psql -U setlist -d setlist_sandbox -Atc "SELECT 1 FROM information_schema.tables WHERE table_name='groups'" | grep -q 1; then
  SETLIST_SANDBOX_DIR="$PWD" bash scripts/backup-sandbox.sh
fi
docker compose -f compose.sandbox.yaml run --rm --no-deps migrate
if [[ ! -f .env.admin ]]; then
  docker compose -f compose.sandbox.yaml run --rm --no-deps \
    --user "$(id -u):$(id -g)" -v "$PWD:/config" \
    --entrypoint /app/.venv/bin/python migrate \
    -m setlist_bot.admin --create-config /config/.env.admin
fi
mkdir -p .admin-status
chmod 755 .admin-status
python3 scripts/admin-host-status.py --directory "$PWD"
docker compose -f compose.sandbox.yaml -f compose.admin.sandbox.yaml up -d --no-build --no-deps admin
printf '\nWeb UI prepared on 127.0.0.1:8088. The polling bot has NOT been started.\n'
printf 'Stop the old sandbox polling instance before starting bot on this host.\n'
