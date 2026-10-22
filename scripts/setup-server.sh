#!/usr/bin/env bash
# Fresh Ubuntu server installer. Does not restore or delete an existing database.
set +x
set -euo pipefail
umask 077

if [[ "${1:-}" == "--help" ]]; then
    printf '%s\n' 'Fresh Ubuntu setup: run as root from an interactive console.' \
        'Installs Docker, asks for BOT_TOKEN privately, prepares a clean database,' \
        'checks Telegram, asks you to confirm the old bot is stopped, then starts.' \
        'Existing data are never deleted. Local backups run daily; retention: 14 days.'
    exit 0
fi
[[ $# == 0 ]] || { printf 'Unknown argument. Use --help.\n' >&2; exit 1; }
[[ $EUID == 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
[[ -t 0 ]] || { printf 'An interactive server console is required.\n' >&2; exit 1; }
app_dir=/opt/setlist_bot_codex
trap 'printf "Setup stopped at line %s. Existing database and settings are preserved.\n" "$LINENO" >&2' ERR

# The temporary key supplied in the setup conversation is no longer needed.
if [[ -f /root/.ssh/authorized_keys ]]; then
    sed -i '/ setlist-deploy-temporary$/d' /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi

printf '\n[1/7] Installing server dependencies\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3 openssl
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    apt-get install -y docker.io docker-compose-v2
fi
systemctl enable --now docker

printf '\n[2/7] Loading the project\n'
if [[ -d "$app_dir/.git" ]]; then
    git -C "$app_dir" diff --quiet
    git -C "$app_dir" diff --cached --quiet
    git -C "$app_dir" pull --ff-only origin main
elif [[ -e "$app_dir" ]]; then
    printf 'Directory already exists without Git; nothing overwritten: %s\n' "$app_dir" >&2
    exit 1
else
    git clone --branch main --single-branch https://github.com/shigarovms/setlist_bot_codex.git "$app_dir"
fi
cd "$app_dir"
if [[ ! -f .env ]] && docker volume inspect setlist_bot_codex_postgres_data >/dev/null 2>&1; then
    printf 'A database volume exists without its .env. Recover its settings first; no data deleted.\n' >&2
    exit 1
fi

printf '\n[3/7] Preparing private settings\n'
python3 - <<'PY'
import getpass
import os
import re
import secrets
from pathlib import Path

path = Path('.env')
if path.exists():
    os.chmod(path, 0o600)
    values = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', values.get('BOT_TOKEN', '')):
        raise SystemExit('Existing .env has no valid BOT_TOKEN. Edit it locally with nano; do not share it.')
    if not re.fullmatch(r'[a-fA-F0-9]{32,}', values.get('POSTGRES_PASSWORD', '')):
        raise SystemExit('Existing .env has no hex database password. Nothing overwritten.')
    if values.get('COMPOSE_FILE') != 'compose.yaml:compose.production.yaml':
        with path.open('a') as stream:
            stream.write('\nCOMPOSE_FILE=compose.yaml:compose.production.yaml\n')
    print('Existing private settings preserved.')
else:
    token = getpass.getpass('Введите BOT_TOKEN от BotFather (ввод скрыт): ').strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise SystemExit('Invalid token format; settings were not written.')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(f'BOT_TOKEN={token}\nPOSTGRES_PASSWORD={secrets.token_hex(32)}\n')
        stream.write('COMPOSE_FILE=compose.yaml:compose.production.yaml\n')
        stream.write('BOT_COMMAND_LEGACY_LANGUAGES=[]\nBOT_COMMAND_EXTRA_SCOPES=[]\n')
    print('Private settings created with mode 0600. Token is not printed.')
PY
compose=(docker compose -f compose.yaml -f compose.production.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" build

printf '\n[4/7] Checking Telegram from the application container (without polling)\n'
"${compose[@]}" run --rm --no-deps -T --entrypoint .venv/bin/python bot - <<'PY'
import asyncio
from aiogram import Bot
from setlist_bot.config import Settings
from setlist_bot.transport.session import VerifiedSession

async def check():
    settings = Settings()
    async with Bot(settings.bot_token.get_secret_value(), session=VerifiedSession()) as bot:
        for attempt in range(3):
            try:
                me = await bot.get_me(request_timeout=15)
                print(f'Telegram OK: @{me.username}')
                return
            except Exception as error:
                if attempt == 2:
                    raise SystemExit(f'Telegram check failed ({type(error).__name__}); polling was not started.') from None
                await asyncio.sleep(2)
asyncio.run(check())
PY

printf '\n[5/7] Initializing the database without importing data\n'
"${compose[@]}" up -d --wait db
"${compose[@]}" run --rm --no-deps -T migrate
rows="$("${compose[@]}" exec -T db psql -U setlist -d setlist -Atc \
    'SELECT (SELECT count(*) FROM groups) + (SELECT count(*) FROM users) + (SELECT count(*) FROM concerts) + (SELECT count(*) FROM songs);')"
if [[ "$rows" != 0 ]]; then
    printf 'Existing application data found. Fresh setup stopped; no data were deleted.\n' >&2
    exit 1
fi

printf '\n[6/7] Preparing daily local backups\n'
cat > /etc/systemd/system/setlist-backup.service <<'UNIT'
[Unit]
Description=Setlist PostgreSQL local backup
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/setlist_bot_codex
ExecStart=/bin/bash /opt/setlist_bot_codex/scripts/backup-server.sh
UMask=0077
UNIT
cat > /etc/systemd/system/setlist-backup.timer <<'UNIT'
[Unit]
Description=Daily Setlist backup at 03:30 Moscow time

[Timer]
OnCalendar=*-*-* 00:30:00 UTC
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl start setlist-backup.service
systemctl enable --now setlist-backup.timer

printf '\n[7/7] Starting a single polling instance\n'
printf 'Остановите старый сервер/бота в панели Timeweb. Два экземпляра с одним токеном запускать нельзя.\n'
read -r -p 'Старый экземпляр остановлен? Введите STOPPED: ' answer < /dev/tty
[[ "$answer" == STOPPED ]] || { printf 'New bot not started. Database and settings are ready.\n'; exit 1; }
"${compose[@]}" up -d --no-build bot
"${compose[@]}" exec -T bot .venv/bin/setlist-bot --check
for attempt in {1..45}; do
    if "${compose[@]}" logs --no-color --tail 80 bot 2>&1 | grep -Fq 'Run polling for bot @'; then
        "${compose[@]}" ps -a
        systemctl list-timers --no-pager setlist-backup.timer
        printf '\nBot polling started. Test /start in Telegram. No previous application data were imported.\n'
        printf 'Local backups are in %s/backups (14 days). External backup storage is not configured.\n' "$app_dir"
        exit 0
    fi
    sleep 2
done
printf 'Polling startup not confirmed. Inspect locally: docker compose logs --tail 50 bot\n' >&2
exit 1
