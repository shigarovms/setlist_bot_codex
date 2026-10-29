"""Connect the existing sandbox-hosted console to production without copying bot secrets.

RU: Запускать Python 3 на VPS из /opt/setlist_bot_sandbox. Секрет новой роли
создаётся локально, передаётся PostgreSQL через stdin и пишется только в файл 600.
EN: No PostgreSQL port is published and no application data is restored or copied.
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

PRODUCTION = Path("/opt/setlist_bot_codex")
SANDBOX = Path("/opt/setlist_bot_sandbox")
ROLE = "setlist_web_admin"
CONTAINER = "setlist_bot_codex-db-1"
SCHEMA = "d89c3e17a024"
READ_TABLES = (
    "users",
    "groups",
    "memberships",
    "chat_bindings",
    "concerts",
    "setlists",
    "items",
    "blocks",
    "songs",
    "revisions",
    "outbox",
    "support_cases",
    "support_messages",
    "admin_audit",
    "runtime_status",
    "admin_sessions",
    "admin_login_limits",
    "deployment_identity",
    "alembic_version",
    "processed_updates",
    "confirmations",
)
AUTH_TABLES = {
    "admin_sessions": "INSERT,DELETE",
    "admin_login_limits": "INSERT,UPDATE",
    "admin_audit": "INSERT",
}
UPDATES = {
    "groups": "owner_id,reminders_enabled,deleted_at,deleted_chat_id,lifecycle_version",
    "memberships": "role",
    "concerts": "deleted_at,lifecycle_version",
    "outbox": "sent,discarded_at,next_attempt_at",
    # Audit actor/action/time/nonce stay immutable; the transaction adds result metadata.
    "admin_audit": "details",
}


def grants(role, database, actions):
    if not re.fullmatch("[a-z_][a-z_0-9]*", role) or not re.fullmatch("[a-z_][a-z_0-9]*", database):
        raise ValueError("Invalid database or role identifier")
    statements = [
        f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"',
        f'GRANT USAGE ON SCHEMA public TO "{role}"',
        f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{role}"',
        "GRANT SELECT ON " + ",".join(READ_TABLES) + f' TO "{role}"',
        f'GRANT SELECT(id) ON personal_drafts TO "{role}"',
    ]
    statements += [
        f'GRANT {permissions} ON {table} TO "{role}"' for table, permissions in AUTH_TABLES.items()
    ]
    # Table-level REVOKE does not remove PostgreSQL column-level UPDATE grants.
    statements += [
        f'REVOKE UPDATE({columns}) ON {table} FROM "{role}"' for table, columns in UPDATES.items()
    ]
    if actions:
        statements += [
            f'GRANT UPDATE({columns}) ON {table} TO "{role}"' for table, columns in UPDATES.items()
        ]
        statements += [
            f'GRANT INSERT,UPDATE ON support_cases TO "{role}"',
            f'GRANT INSERT ON outbox,chat_bindings TO "{role}"',
        ]
    return ";\n".join(statements) + ";\n"


def configured_values(contents):
    values = {}
    for line in contents.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def updated_configuration(contents, password, actions):
    updates = {
        "WEB_ADMIN_PRODUCTION_DATABASE_URL": (
            f"postgresql+asyncpg://{ROLE}:{quote(password, safe='')}@{CONTAINER}:5432/setlist"
        ),
        "WEB_ADMIN_ALLOW_PRODUCTION": "true",
        "WEB_ADMIN_PRODUCTION_READ_ONLY": "false" if actions else "true",
    }
    lines = [
        line for line in contents.splitlines() if line.partition("=")[0].strip() not in updates
    ]
    return "\n".join(lines + [key + "=" + value for key, value in updates.items()]) + "\n"


def saved_password(contents):
    url = urlsplit(configured_values(contents).get("WEB_ADMIN_PRODUCTION_DATABASE_URL", ""))
    if (
        url.username == ROLE
        and url.hostname == CONTAINER
        and url.path == "/setlist"
        and re.fullmatch("[0-9a-f]{64}", url.password or "")
    ):
        return url.password
    return None


def run(command, *, cwd, data=None):
    result = subprocess.run(command, cwd=cwd, input=data, text=True, capture_output=True)
    if result.returncode:
        # RU/EN: Never echo a failed SQL statement or configuration containing a password.
        raise RuntimeError("Command failed; stage aborted without printing secret input/output.")
    return result.stdout.strip()


def sql(statement):
    return run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.production.yaml",
            "exec",
            "-T",
            "db",
            "psql",
            "-X",
            "-U",
            "setlist",
            "-d",
            "setlist",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-A",
            "-t",
        ],
        cwd=PRODUCTION,
        data=statement,
    )


def configure_host_status():
    run(
        [
            sys.executable,
            str(PRODUCTION / "scripts/admin-host-status.py"),
            "--directory",
            str(PRODUCTION),
        ],
        cwd=PRODUCTION,
    )
    # Separate units: never replace the production database backup timer.
    units = Path("/etc/systemd/system")
    (units / "setlist-production-admin-status.service").write_text(
        "[Unit]\nDescription=Setlist production host metadata\n[Service]\nType=oneshot\n"
        f"WorkingDirectory={PRODUCTION}\n"
        f"ExecStart=/usr/bin/python3 {PRODUCTION}/scripts/admin-host-status.py "
        f"--directory {PRODUCTION}\n"
    )
    (units / "setlist-production-admin-status.timer").write_text(
        "[Unit]\nDescription=Update production admin host status\n[Timer]\n"
        "OnBootSec=30s\nOnUnitActiveSec=60s\n[Install]\nWantedBy=timers.target\n"
    )
    run(["systemctl", "daemon-reload"], cwd=PRODUCTION)
    run(["systemctl", "enable", "--now", "setlist-production-admin-status.timer"], cwd=PRODUCTION)


def verify_console():
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            for prefix in ("sandbox", "production"):
                with urlopen(f"http://127.0.0.1:8088/{prefix}/healthz", timeout=3) as response:
                    if json.load(response) != {"status": "ok"}:
                        raise ValueError("Unexpected health response")
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError("Console did not become ready for both databases; inspect admin logs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-actions", action="store_true")
    args = parser.parse_args()
    if Path(__file__).resolve().parents[1] != SANDBOX or Path.cwd().resolve() != SANDBOX:
        raise SystemExit("Run only from /opt/setlist_bot_sandbox on the VPS.")
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the VPS to configure private files and metadata timer.")
    path = SANDBOX / ".env.admin"
    if not path.is_file():
        raise SystemExit("Existing private .env.admin is required.")
    if sql("SELECT version_num FROM alembic_version;") != SCHEMA:
        raise SystemExit("Production migrations must be updated before connecting the console.")
    identity = sql(
        "SELECT bot_id::text||':'||environment FROM deployment_identity WHERE key='main';"
    )
    if identity != "7430933141:production":
        raise SystemExit("Production database is not bound to the expected bot/environment.")
    if args.enable_actions:
        ready = sql(
            "SELECT count(*) FROM runtime_status WHERE component='bot' AND state<>'stopped' "
            "AND updated_at>now()-interval '100 seconds' AND details->>'bot_id'='7430933141' "
            f"AND details->>'environment'='production' AND details->>'schema'='{SCHEMA}';"
        )
        if ready != "1":
            raise SystemExit("Wait for a fresh compatible production bot signal, then retry.")
    contents = path.read_text()
    pending = path.with_name(".env.admin.production-pending")
    exists = sql(f"SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{ROLE}');") == "t"
    password = None
    if exists:
        password = saved_password(contents)
        if password is None and pending.is_file():
            password = saved_password(pending.read_text())
        if password is None:
            raise SystemExit(
                "Dedicated role already exists but its private configuration is unavailable; "
                "refusing to reset it."
            )
        flags = json.loads(
            sql(
                "SELECT json_build_array(rolsuper,rolcreatedb,rolcreaterole,rolreplication,"
                f"rolbypassrls)::text FROM pg_roles WHERE rolname='{ROLE}';"
            )
        )
        if any(flags):
            raise SystemExit(
                "Existing role has unexpected elevated privileges; refusing to use it."
            )
    password = password or secrets.token_hex(32)
    creation = (
        ""
        if exists
        else f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}';\n"
    )
    print("Configuring dedicated production database access...", flush=True)
    backup = path.with_name(
        ".env.admin.before-production-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    backup.touch(mode=0o600, exist_ok=False)
    backup.write_text(contents)
    descriptor, temporary = tempfile.mkstemp(prefix=".env.admin.", dir=SANDBOX)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(updated_configuration(contents, password, args.enable_actions))
        # Keep recoverable credentials if PostgreSQL succeeds but final file replacement fails.
        os.replace(temporary, pending)
        sql("BEGIN;\n" + creation + grants(ROLE, "setlist", args.enable_actions) + "COMMIT;\n")
        os.replace(pending, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.chmod(path, 0o600)
    configure_host_status()
    compose = [
        "docker",
        "compose",
        "-f",
        "compose.sandbox.yaml",
        "-f",
        "compose.admin.sandbox.yaml",
        "-f",
        "compose.admin.production-access.yaml",
    ]
    run(compose + ["config", "--quiet"], cwd=SANDBOX)
    run(compose + ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "admin"], cwd=SANDBOX)
    verify_console()
    print(
        "Production connected. Mode: "
        + ("administration" if args.enable_actions else "read-only")
        + ". Login/password unchanged."
    )
    print("Private configuration and backup remain on this VPS; do not publish them.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError):
        raise SystemExit(
            "Connection setup stopped. No secrets printed; "
            "inspect this step locally before retrying."
        ) from None
