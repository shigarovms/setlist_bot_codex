#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

app_dir="${SETLIST_APP_DIR:-/opt/setlist_bot_codex}"
cd "$app_dir"
mkdir -p backups
chmod 700 backups
temporary="$(mktemp backups/.backup-XXXXXXXX.dump)"
trap 'rm -f "$temporary"' EXIT

compose=(docker compose -f compose.yaml -f compose.production.yaml)
"${compose[@]}" exec -T db pg_dump -U setlist --format=custom setlist > "$temporary"
"${compose[@]}" exec -T db pg_restore --list < "$temporary" > /dev/null
destination="backups/setlist-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
mv "$temporary" "$destination"
chmod 600 "$destination"
# Only remove our completed dumps after a new dump has passed archive validation.
find backups -maxdepth 1 -type f -name 'setlist-*.dump' -mtime +13 -delete
printf 'Backup saved: %s\n' "$destination"
