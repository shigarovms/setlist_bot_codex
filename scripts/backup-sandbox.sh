#!/usr/bin/env bash
# This script is intentionally independent of the production backup script.
set +x
set -euo pipefail
umask 077
sandbox_dir="${SETLIST_SANDBOX_DIR:-/opt/setlist_bot_sandbox}"
cd "$sandbox_dir"
mkdir -p backups
chmod 700 backups
temporary="$(mktemp backups/.sandbox-XXXXXXXX.dump)"
trap 'rm -f "$temporary"' EXIT
docker compose -f compose.sandbox.yaml exec -T db pg_dump -U setlist -d setlist_sandbox -Fc > "$temporary"
docker compose -f compose.sandbox.yaml exec -T db pg_restore --list < "$temporary" > /dev/null
destination="backups/setlist-sandbox-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
mv "$temporary" "$destination"
printf 'Sandbox backup saved: %s\n' "$destination"
