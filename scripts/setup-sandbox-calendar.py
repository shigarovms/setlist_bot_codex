"""RU: Только тестовый VPS, без переноса БД. EN: Configure a dedicated read-only feed role."""

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

ROOT = Path("/opt/setlist_bot_sandbox")
ROLE = "setlist_calendar_reader"
SCHEMA = "b93e2a6f104c"
TABLES = (
    "calendar_subscriptions",
    "concerts",
    "deployment_identity",
    "alembic_version",
)


def run(args, *, stdin=None, capture=False):
    return subprocess.run(args, input=stdin, text=True, check=True, capture_output=capture)


def sql(statement):
    return run(
        [
            "docker",
            "exec",
            "-i",
            "setlist-sandbox-db-1",
            "psql",
            "-X",
            "-U",
            "setlist",
            "-d",
            "setlist_sandbox",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
        ],
        stdin=statement,
        capture=True,
    ).stdout.strip()


def values(path):
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                result[key.strip()] = value.strip().strip("\"'")
    return result


def private_write(path, contents):
    descriptor, temporary = tempfile.mkstemp(prefix=".env.calendar-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(contents)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def grants():
    # RU/EN: This role cannot read personal drafts, Telegram IDs, messages or admin sessions.
    return (
        f"GRANT CONNECT ON DATABASE setlist_sandbox TO {ROLE};\n"
        f"GRANT USAGE ON SCHEMA public TO {ROLE};\n"
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};\n"
        f"GRANT SELECT ON {','.join(TABLES)} TO {ROLE};\n"
        f"GRANT SELECT(id,name,deleted_at) ON groups TO {ROLE};\n"
        f"GRANT SELECT(group_id,user_id,status) ON memberships TO {ROLE};\n"
        f"ALTER ROLE {ROLE} SET default_transaction_read_only = on;\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="212-192-217-128.sslip.io")
    args = parser.parse_args()
    host = args.host.lower()
    if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", host):
        raise SystemExit("Use a DNS hostname without scheme, port or path.")
    if (
        Path.cwd().resolve() != ROOT
        or Path(__file__).resolve().parents[1] != ROOT
        or os.geteuid() != 0
    ):
        raise SystemExit("Run as root from /opt/setlist_bot_sandbox on the VPS.")
    if run(["git", "status", "--porcelain"], capture=True).stdout.strip():
        raise SystemExit("Preserve local source changes before setup; working tree must be clean.")
    if sql("SELECT version_num FROM alembic_version;") != SCHEMA:
        raise SystemExit("Update the sandbox and migrate its own database before calendar setup.")
    if (
        sql("SELECT bot_id::text||':'||environment FROM deployment_identity WHERE key='main';")
        != "1110437563:sandbox"
    ):
        raise SystemExit("Wrong database identity; production is never configured by this script.")
    path = ROOT / ".env.calendar"
    previous = values(path)
    password = None
    if previous.get("CALENDAR_DATABASE_URL"):
        parsed = urlsplit(
            previous["CALENDAR_DATABASE_URL"].replace("postgresql+asyncpg:", "postgresql:", 1)
        )
        if parsed.username != ROLE or parsed.path != "/setlist_sandbox" or parsed.hostname != "db":
            raise SystemExit("Existing calendar connection differs; refusing to overwrite it.")
        password = parsed.password
    exists = sql(f"SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{ROLE}');") == "t"
    if exists:
        if not password:
            raise SystemExit(
                "Calendar role exists but private configuration is missing; "
                "refusing a password reset."
            )
        flags = json.loads(
            sql(
                "SELECT json_build_array(rolsuper,rolcreatedb,rolcreaterole,"
                "rolreplication,rolbypassrls)::text "
                f"FROM pg_roles WHERE rolname='{ROLE}';"
            )
        )
        if any(flags):
            raise SystemExit("Existing role has elevated privileges; refusing to use it.")
    password = password or secrets.token_hex(32)
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise SystemExit("Unexpected stored credential format; no changes made.")
    compose = [
        "docker",
        "compose",
        "-f",
        "compose.sandbox.yaml",
        "-f",
        "compose.admin.sandbox.yaml",
        "-f",
        "compose.calendar.sandbox.yaml",
    ]
    admin = values(ROOT / ".env.admin")
    if admin.get("WEB_ADMIN_ALLOW_PRODUCTION", "").lower() in ("true", "1", "yes", "on"):
        compose += ["-f", "compose.admin.production-access.yaml"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    env_path = ROOT / ".env"
    contents = env_path.read_text()
    private_write(ROOT / (".env.before-calendar-" + stamp), contents)
    if path.exists():
        private_write(ROOT / (".env.calendar.before-" + stamp), path.read_text())
    private_write(
        path,
        f"CALENDAR_DATABASE_URL=postgresql+asyncpg://{ROLE}:{quote(password, safe='')}"
        "@db:5432/setlist_sandbox\n"
        "CALENDAR_EXPECTED_BOT_ID=1110437563\nCALENDAR_ENVIRONMENT=sandbox\n"
        "CALENDAR_BOT_USERNAME=botToPract696_bot\n",
    )
    creation = (
        ""
        if exists
        else (
            f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"NOBYPASSRLS PASSWORD '{password}';\n"
        )
    )
    sql("BEGIN;\n" + creation + grants() + "COMMIT;\n")
    replacements = {"CALENDAR_HOST": host, "CALENDAR_PUBLIC_BASE_URL": "https://" + host}
    lines = [
        line for line in contents.splitlines() if line.partition("=")[0].strip() not in replacements
    ]
    private_write(
        env_path,
        "\n".join(lines)
        + "\n"
        + "\n".join(f"{key}={value}" for key, value in replacements.items())
        + "\n",
    )
    run(compose + ["config", "--quiet"])
    run(compose + ["pull", "calendar_proxy"])
    run(compose + ["up", "-d", "--no-build", "calendar", "calendar_proxy"])
    print("Waiting for public HTTPS. TCP ports 80 and 443 must reach this VPS.", flush=True)
    ready = False
    for _attempt in range(30):
        try:
            with urlopen("https://" + host + "/calendar/check.ics", timeout=3) as response:
                ready = response.status == 404
        except HTTPError as exc:
            ready = exc.code == 404
        except (URLError, TimeoutError):
            pass
        if ready:
            break
        time.sleep(2)
    if not ready:
        raise SystemExit(
            "HTTPS is not ready. Check hostname and cloud firewall ports 80/443, then rerun setup. "
            "Bot was not recreated by this step."
        )
    # RU: Конфигурация включается у бота только после успешного HTTPS.
    # EN: The script never starts a second polling host; cloud polling must stay stopped.
    run(compose + ["up", "-d", "--no-build", "--no-deps", "bot"])
    run(compose + ["ps", "bot", "calendar", "calendar_proxy"])
    print("Sandbox calendar HTTPS ready: https://" + host)
    print(
        "Use /calendar in the test bot's private chat. "
        "Production and its database were not changed."
    )


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError):
        raise SystemExit(
            "Calendar setup stopped. No SQL credentials printed; "
            "inspect the failed step locally and preserve configuration."
        ) from None
