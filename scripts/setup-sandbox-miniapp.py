"""RU: Включить редактор только в sandbox. EN: Reuse HTTPS, never import a database."""

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path("/opt/setlist_bot_sandbox")
ROLE = "setlist_miniapp"
SCHEMA = "c04f7a92d531"
READ_TABLES = (
    "users",
    "groups",
    "memberships",
    "concerts",
    "setlists",
    "blocks",
    "items",
    "revisions",
    "songs",
    "personal_drafts",
    "confirmations",
    "alembic_version",
    "deployment_identity",
)


def grants():
    # RU: Только личные черновики, служебная сессия и журнал. Общий сет-лист — read-only.
    # EN: UPDATE(id) is required by PostgreSQL for SELECT FOR UPDATE on tenant/role rows;
    # the application never mutates IDs. No catalog, approval or support writes are granted.
    return (
        f"GRANT CONNECT ON DATABASE setlist_sandbox TO {ROLE};\n"
        f"GRANT USAGE ON SCHEMA public TO {ROLE};\n"
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};\n"
        f"GRANT SELECT ON {','.join(READ_TABLES)} TO {ROLE};\n"
        f"GRANT UPDATE(id) ON groups,memberships TO {ROLE};\n"
        f"GRANT INSERT,UPDATE(content,version) ON personal_drafts TO {ROLE};\n"
        f"GRANT UPDATE(command,expires_at) ON confirmations TO {ROLE};\n"
        f"GRANT INSERT ON admin_audit TO {ROLE};\n"
        f"ALTER ROLE {ROLE} SET statement_timeout='10s';\n"
        f"ALTER ROLE {ROLE} CONNECTION LIMIT 5;\n"
    )


def calendar_helpers():
    spec = importlib.util.spec_from_file_location(
        "calendar_setup", ROOT / "scripts/setup-sandbox-calendar.py"
    )
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    return helpers


def main():
    if (
        Path.cwd().resolve() != ROOT
        or Path(__file__).resolve().parents[1] != ROOT
        or os.geteuid() != 0
    ):
        raise SystemExit("Run as root from /opt/setlist_bot_sandbox on the VPS.")
    # Share safe local file/psql helpers; do not call calendar setup or change its secrets.
    helpers = calendar_helpers()
    run, sql, values, private_write = (
        helpers.run,
        helpers.sql,
        helpers.values,
        helpers.private_write,
    )
    if run(["git", "status", "--porcelain"], capture=True).stdout.strip():
        raise SystemExit("Preserve local changes; working tree must be clean.")
    if sql("SELECT version_num FROM alembic_version;") != SCHEMA:
        raise SystemExit("Update the sandbox first. Unexpected schema; no setup performed.")
    if (
        sql("SELECT bot_id::text||':'||environment FROM deployment_identity WHERE key='main';")
        != "1110437563:sandbox"
    ):
        raise SystemExit("Wrong database identity; production is never configured here.")
    env = values(ROOT / ".env")
    token = env.get("SANDBOX_BOT_TOKEN", "")
    if token.split(":", 1)[0] != "1110437563":
        raise SystemExit("Unexpected test bot identity.")
    origin = env.get("CALENDAR_PUBLIC_BASE_URL", "").rstrip("/")
    host = env.get("CALENDAR_HOST", "")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", host)
        or origin != "https://" + host
        or not (ROOT / ".env.calendar").is_file()
    ):
        raise SystemExit("Configure and verify sandbox calendar HTTPS first.")
    path = ROOT / ".env.miniapp"
    previous = values(path)
    password = None
    if previous.get("MINIAPP_DATABASE_URL"):
        parsed = urlsplit(
            previous["MINIAPP_DATABASE_URL"].replace("postgresql+asyncpg:", "postgresql:", 1)
        )
        if parsed.username != ROLE or parsed.hostname != "db" or parsed.path != "/setlist_sandbox":
            raise SystemExit("Existing connection differs; refusing to overwrite it.")
        password = parsed.password
    exists = sql(f"SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{ROLE}');") == "t"
    if exists:
        if not password:
            raise SystemExit("Role exists without private configuration; refusing password reset.")
        flags = json.loads(
            sql(
                "SELECT json_build_array(rolsuper,rolcreatedb,rolcreaterole,rolreplication,"
                f"rolbypassrls)::text FROM pg_roles WHERE rolname='{ROLE}';"
            )
        )
        if any(flags):
            raise SystemExit("Existing role has elevated privileges; refusing to use it.")
    password = password or secrets.token_hex(32)
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise SystemExit("Unexpected stored credential format; stopping.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    contents = (ROOT / ".env").read_text()
    private_write(ROOT / (".env.before-miniapp-" + stamp), contents)
    if path.exists():
        private_write(ROOT / (".env.miniapp.before-" + stamp), path.read_text())
    # RU: Это только ключ проверки initData, не токен доступа к Bot API.
    # EN: The public process cannot call Telegram with this derived validation key.
    key = base64.urlsafe_b64encode(hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest())
    private_write(
        path,
        (
            f"MINIAPP_DATABASE_URL=postgresql+asyncpg://{ROLE}:{password}@db:5432/setlist_sandbox\n"
            "MINIAPP_EXPECTED_BOT_ID=1110437563\nMINIAPP_ENVIRONMENT=sandbox\n"
            "MINIAPP_BOT_USERNAME=botToPract696_bot\n"
            f"MINIAPP_PUBLIC_BASE_URL={origin}\nMINIAPP_AUTH_KEY={key.decode()}\n"
        ),
    )
    create = (
        ""
        if exists
        else (
            f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"NOBYPASSRLS PASSWORD '{password}';\n"
        )
    )
    sql("BEGIN;\n" + create + grants() + "COMMIT;\n")
    lines = [
        line
        for line in contents.splitlines()
        if line.partition("=")[0].strip() != "MINIAPP_PUBLIC_BASE_URL"
    ]
    private_write(ROOT / ".env", "\n".join(lines) + f"\nMINIAPP_PUBLIC_BASE_URL={origin}\n")
    compose = [
        "docker",
        "compose",
        "-f",
        "compose.sandbox.yaml",
        "-f",
        "compose.admin.sandbox.yaml",
        "-f",
        "compose.calendar.sandbox.yaml",
        "-f",
        "compose.miniapp.sandbox.yaml",
    ]
    admin = values(ROOT / ".env.admin")
    if admin.get("WEB_ADMIN_ALLOW_PRODUCTION", "").lower() in ("true", "1", "yes", "on"):
        compose += ["-f", "compose.admin.production-access.yaml"]
    run(compose + ["config", "--quiet"])
    run(compose + ["up", "-d", "--no-build", "miniapp"])
    ready = False
    for _attempt in range(15):
        probe = subprocess.run(
            compose
            + [
                "exec",
                "-T",
                "miniapp",
                ".venv/bin/python",
                "-c",
                "import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:8091/healthz',timeout=3)",
            ],
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            ready = True
            break
        time.sleep(2)
    if not ready:
        raise SystemExit("Mini App unhealthy; bot was not recreated. Inspect miniapp logs.")
    run(compose + ["up", "-d", "--no-build", "--force-recreate", "calendar_proxy"])
    with urlopen(origin + "/miniapp/", timeout=10) as response:
        if response.status != 200:
            raise SystemExit("Public Mini App is unavailable; bot was not recreated.")
    run(compose + ["up", "-d", "--no-build", "--no-deps", "bot"])
    run(compose + ["ps", "bot", "miniapp", "calendar", "calendar_proxy"])
    print("Sandbox Mini App enabled. Open a personal draft and choose the HTML editor.")
    print("Production, its database and the admin connection were not changed.")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError):
        raise SystemExit(
            "Mini App setup stopped. No credentials printed; inspect locally."
        ) from None
