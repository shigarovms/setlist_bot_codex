#!/usr/bin/env bash
# RU: Выпуск основной версии с репетицией на копии. EN: Never restore into live setlist.
set -euo pipefail
umask 077
cd -P "$(dirname "$0")/.."
if [[ "$PWD" != /opt/setlist_bot_codex || "$(git branch --show-current)" != main ]]; then
  echo "Run only from /opt/setlist_bot_codex on branch main."
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree must be clean; preserve local changes before releasing."
  exit 1
fi
compose=(docker compose -f compose.yaml -f compose.production.yaml)
"${compose[@]}" config --quiet
identity="$("${compose[@]}" exec -T bot .venv/bin/python -c 'from setlist_bot.config import Settings; token=Settings().bot_token; print(token.get_secret_value().split(":",1)[0] if token else "missing")')"
if [[ "$identity" != 7430933141 ]]; then
  echo "Unexpected production bot identity; stopping."
  exit 1
fi
"${compose[@]}" build --build-arg "APP_REVISION=$(git rev-parse HEAD)" bot migrate

# First backup is restored only into a newly created rehearsal database.
backup_output="$(SETLIST_APP_DIR="$PWD" bash scripts/backup-server.sh)"
echo "$backup_output"
release_dump="${backup_output#Backup saved: }"
if [[ "$release_dump" != backups/setlist-*.dump || ! -s "$release_dump" ]]; then
  echo "Unexpected backup output; stopping."
  exit 1
fi
rehearsal_db="setlist_admin_release_$(date -u +%Y%m%d%H%M%S)_$$_test"
created=false
stopped=false
cleanup() {
  outcome=$?
  trap - EXIT
  if [[ "$created" == true && "$rehearsal_db" == setlist_admin_release_*_test ]]; then
    "${compose[@]}" exec -T db dropdb -U setlist "$rehearsal_db" || echo "Temporary rehearsal database requires cleanup: $rehearsal_db"
  fi
  if [[ "$outcome" != 0 && "$stopped" == true ]]; then
    echo "Release stopped after bot shutdown. Keep the backup; inspect the error before starting any version."
  fi
  exit "$outcome"
}
trap cleanup EXIT
"${compose[@]}" exec -T db createdb -U setlist "$rehearsal_db"
created=true
"${compose[@]}" exec -T db pg_restore -U setlist --no-owner --no-acl \
  --exit-on-error --single-transaction -d "$rehearsal_db" < "$release_dump"
"${compose[@]}" run --rm --no-deps -T -e "RELEASE_CHECK_DATABASE=$rehearsal_db" \
  --entrypoint .venv/bin/python migrate - <<'PY'
import asyncio
import os
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from setlist_bot.infrastructure.runtime import SCHEMA_REVISION

target = os.environ['RELEASE_CHECK_DATABASE']
assert target.startswith('setlist_admin_release_') and target.endswith('_test')
original = make_url(os.environ['DATABASE_URL'])
assert original.database == 'setlist'
os.environ['DATABASE_URL'] = original.set(database=target).render_as_string(hide_password=False)
tables = ('users','groups','songs','concerts','revisions','personal_drafts','export_files')
async def inspect_copy():
    engine = create_async_engine(os.environ['DATABASE_URL'])
    try:
        async with engine.connect() as connection:
            counts = [int(await connection.scalar(text(f'SELECT count(*) FROM {name}'))) for name in tables]
            version = await connection.scalar(text('SELECT version_num FROM alembic_version'))
            return counts, version
    finally:
        await engine.dispose()
before, _ = asyncio.run(inspect_copy())
command.upgrade(Config('alembic.ini'), 'head')
after, version = asyncio.run(inspect_copy())
assert before == after, 'Application row counts changed during migration rehearsal'
assert version == SCHEMA_REVISION
print('Production-copy migration rehearsal OK; application rows preserved.', flush=True)
PY
"${compose[@]}" exec -T db dropdb -U setlist "$rehearsal_db"
created=false

# No production downtime until the rehearsal has succeeded. Take a final fresh copy.
"${compose[@]}" stop bot
stopped=true
SETLIST_APP_DIR="$PWD" bash scripts/backup-server.sh
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps --entrypoint .venv/bin/setlist-bot migrate --check
"${compose[@]}" up -d --no-build --no-deps bot
stopped=false
"${compose[@]}" exec -T bot .venv/bin/setlist-bot --check
"${compose[@]}" ps
"${compose[@]}" logs --tail 25 bot
echo "Production code updated. Verify /start and fresh runtime status before enabling admin actions."
