"""Enable production calendar/Mini App behind the VPS's existing Caddy, without DB copying."""

import argparse
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

ROOT = Path("/opt/setlist_bot_codex")
SANDBOX = Path("/opt/setlist_bot_sandbox")
CONTAINER = "setlist_bot_codex-db-1"
SCHEMA = "a1426785384d"
EDGE = "setlist-public-edge"


def module(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).with_name(name + ".py")
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(args, *, stdin=None, capture=False, cwd=None):
    return subprocess.run(
        args, input=stdin, text=True, check=True, capture_output=capture, cwd=cwd or ROOT
    )


def sql(statement):
    # RU: Единственное SQL-подключение установщика — основной контейнер и setlist.
    # EN: Never restore/copy a sandbox database or reuse its PostgreSQL credentials.
    return run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "psql",
            "-X",
            "-U",
            "setlist",
            "-d",
            "setlist",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
        ],
        stdin=statement,
        capture=True,
    ).stdout.strip()


def role_config(kind, origin, token, values, private_write):
    prefix = kind.upper()
    setup = module("setup-sandbox-" + kind)
    role = setup.ROLE
    path = ROOT / (".env." + kind)
    saved = values(path)
    password = None
    if saved.get(prefix + "_DATABASE_URL"):
        target = urlsplit(
            saved[prefix + "_DATABASE_URL"].replace("postgresql+asyncpg:", "postgresql:", 1)
        )
        if target.username != role or target.hostname != "db" or target.path != "/setlist":
            raise SystemExit("Existing production service connection differs; refusing overwrite.")
        password = target.password
    exists = sql(f"SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}');") == "t"
    if exists:
        if not password:
            raise SystemExit(
                "Production HTTP role exists without its private configuration; stopping."
            )
        flags = json.loads(
            sql(
                "SELECT json_build_array(rolsuper,rolcreatedb,rolcreaterole,rolreplication,"
                f"rolbypassrls)::text FROM pg_roles WHERE rolname='{role}';"
            )
        )
        if any(flags):
            raise SystemExit("Production HTTP role has elevated privileges; stopping.")
    password = password or secrets.token_hex(32)
    if not re.fullmatch(r"[0-9a-f]{64}", password):
        raise SystemExit("Unexpected stored role credential format; stopping.")
    entries = {
        prefix + "_DATABASE_URL": f"postgresql+asyncpg://{role}:{password}@db:5432/setlist",
        prefix + "_EXPECTED_BOT_ID": "7430933141",
        prefix + "_ENVIRONMENT": "production",
        prefix + "_BOT_USERNAME": "setListDuration_bot",
    }
    if kind == "miniapp":
        entries["MINIAPP_PUBLIC_BASE_URL"] = origin
        entries["MINIAPP_AUTH_KEY"] = base64.urlsafe_b64encode(
            hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        ).decode()
    if path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        private_write(ROOT / (path.name + ".before-" + stamp), path.read_text())
    # Persist before SQL, so an interrupted retry never resets an unknown password.
    private_write(path, "".join(f"{key}={value}\n" for key, value in entries.items()))
    create = (
        ""
        if exists
        else (
            f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"NOBYPASSRLS PASSWORD '{password}';\n"
        )
    )
    grants = setup.grants().replace("ON DATABASE setlist_sandbox", "ON DATABASE setlist")
    sql("BEGIN;\n" + create + grants + "COMMIT;\n")


def healthy(compose, service, port):
    for _ in range(20):
        try:
            run(
                compose
                + [
                    "exec",
                    "-T",
                    service,
                    ".venv/bin/python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen("
                    f"'http://127.0.0.1:{port}/healthz',timeout=3)",
                ],
                capture=True,
            )
            return
        except subprocess.CalledProcessError:
            time.sleep(2)
    raise SystemExit(f"Production {service} is not healthy; bot has not been recreated by setup.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="setlist-212-192-217-128.sslip.io")
    parser.add_argument("--check-key", action="store_true")
    args = parser.parse_args()
    if (
        Path.cwd().resolve() != ROOT
        or Path(__file__).resolve().parents[1] != ROOT
        or os.geteuid() != 0
    ):
        raise SystemExit("Run as root from /opt/setlist_bot_codex, branch main.")
    if run(["git", "branch", "--show-current"], capture=True).stdout.strip() != "main":
        raise SystemExit("Production setup requires branch main.")
    if run(["git", "status", "--porcelain"], capture=True).stdout.strip():
        raise SystemExit("Preserve local changes; working tree must be clean.")
    helpers = module("setup-sandbox-calendar")
    values, private_write = helpers.values, helpers.private_write
    token = values(ROOT / ".env").get("BOT_TOKEN", "")
    if token.split(":", 1)[0] != "7430933141":
        raise SystemExit("Wrong production token identity; no changes made.")
    if (
        sql("SELECT bot_id::text||':'||environment FROM deployment_identity WHERE key='main';")
        != "7430933141:production"
    ):
        raise SystemExit("Wrong production database identity; no changes made.")
    key_setup = module("calendar_key")

    def check_ciphertexts():
        return (
            sql("SELECT EXISTS(SELECT 1 FROM calendar_subscriptions WHERE token_ciphertext <> '');")
            == "t"
        )

    if args.check_key:
        key_setup.ensure_key(ROOT, check_ciphertexts)
        print("Production calendar key ready; existing key preserved.")
        return
    if sql("SELECT version_num FROM alembic_version;") != SCHEMA:
        raise SystemExit("Release production code/migrations first; unexpected schema.")
    host = args.host.lower()
    if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", host):
        raise SystemExit("Use a DNS hostname, without scheme, port or path.")
    sandbox_values = values(SANDBOX / ".env")
    if not sandbox_values.get("CALENDAR_HOST") or host == sandbox_values["CALENDAR_HOST"]:
        raise SystemExit("Production and sandbox HTTPS hostnames must be distinct.")
    for filename in ("compose.proxy.production-access.yaml", "deploy/Caddyfile.public"):
        if not (SANDBOX / filename).is_file():
            raise SystemExit("Pull the prepared source in /opt/setlist_bot_sandbox first.")
    marker = SANDBOX / ".env.production-public"
    if marker.exists() and values(marker).get("PRODUCTION_PUBLIC_HOST") != host:
        raise SystemExit("An existing production hostname differs; refusing silent URL rotation.")
    origin = "https://" + host
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    contents = (ROOT / ".env").read_text()
    private_write(ROOT / (".env.before-public-" + stamp), contents)
    key_setup.ensure_key(ROOT, check_ciphertexts)
    role_config("calendar", origin, token, values, private_write)
    role_config("miniapp", origin, token, values, private_write)
    try:
        run(["docker", "network", "inspect", EDGE], capture=True)
    except subprocess.CalledProcessError:
        run(["docker", "network", "create", "--driver", "bridge", EDGE], capture=True)
    compose = [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.production.yaml",
        "-f",
        "compose.public.production.yaml",
    ]
    run(compose + ["config", "--quiet"])
    run(compose + ["up", "-d", "--no-build", "--no-deps", "calendar", "miniapp"])
    healthy(compose, "calendar", 8090)
    healthy(compose, "miniapp", 8091)
    # RU: У общего прокси только hostname, никаких токенов/паролей production.
    # EN: Attach only the HTTP edge; never connect Caddy to production's data network.
    private_write(marker, f"PRODUCTION_PUBLIC_HOST={host}\n")
    proxy = [
        "docker",
        "compose",
        "-f",
        "compose.sandbox.yaml",
        "-f",
        "compose.admin.sandbox.yaml",
        "-f",
        "compose.calendar.sandbox.yaml",
    ]
    if (SANDBOX / ".env.miniapp").is_file():
        proxy += ["-f", "compose.miniapp.sandbox.yaml"]
    proxy += ["-f", "compose.proxy.production-access.yaml"]
    admin = values(SANDBOX / ".env.admin")
    if admin.get("WEB_ADMIN_ALLOW_PRODUCTION", "").lower() in ("true", "1", "yes", "on"):
        proxy += ["-f", "compose.admin.production-access.yaml"]
    run(proxy + ["config", "--quiet"], cwd=SANDBOX)
    run(
        proxy + ["up", "-d", "--no-build", "--no-deps", "--force-recreate", "calendar_proxy"],
        cwd=SANDBOX,
    )
    waiter = module("setup-sandbox-miniapp")
    waiter.wait_for_https(origin)
    waiter.wait_for_https("https://" + sandbox_values["CALENDAR_HOST"])
    replacements = {
        "CALENDAR_PUBLIC_BASE_URL": origin,
        "MINIAPP_PUBLIC_BASE_URL": origin,
        "COMPOSE_FILE": "compose.yaml:compose.production.yaml:compose.public.production.yaml",
    }
    lines = [
        line for line in contents.splitlines() if line.partition("=")[0].strip() not in replacements
    ]
    private_write(
        ROOT / ".env",
        "\n".join(lines)
        + "\n"
        + "".join(f"{key}={value}\n" for key, value in replacements.items()),
    )
    run(compose + ["up", "-d", "--no-build", "--no-deps", "bot"])
    run(compose + ["exec", "-T", "bot", ".venv/bin/setlist-bot", "--check"])
    run(compose + ["ps", "bot", "calendar", "miniapp"])
    print("Production calendar and Mini App ready: " + origin)
    print("Production uses its own database, roles and keys; no application data transferred.")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError):
        raise SystemExit(
            "Production public setup stopped. No credentials printed; inspect locally."
        ) from None
