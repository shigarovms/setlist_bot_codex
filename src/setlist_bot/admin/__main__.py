"""Run admin HTTP service or create a private configuration without printing secrets."""

import argparse
import getpass
import os
import secrets
from pathlib import Path

from aiohttp import web

from setlist_bot.admin.console import create_console
from setlist_bot.admin.security import AdminSettings, password_hash


def configure(path: Path) -> None:
    if path.exists():
        raise SystemExit("Configuration already exists; edit it locally or choose another path.")
    username = input("Login [admin]: ").strip() or "admin"
    password = getpass.getpass("New admin password (at least 12 characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    encoded = password_hash(password)
    secret = secrets.token_hex(32)
    settings = AdminSettings(
        _env_file=None,
        database_url="postgresql+asyncpg://setlist:setlist@db:5432/setlist_sandbox",
        username=username,
        password_hash=encoded,
        secret=secret,
    )
    contents = "\n".join(
        [
            "WEB_ADMIN_DATABASE_URL=" + settings.database_url.get_secret_value(),
            "WEB_ADMIN_USERNAME=" + username,
            "WEB_ADMIN_PASSWORD_HASH=" + encoded,
            "WEB_ADMIN_SECRET=" + secret,
            "WEB_ADMIN_HOST=0.0.0.0",
            "WEB_ADMIN_PORT=8080",
            "WEB_ADMIN_ALLOWED_HOSTS=localhost,127.0.0.1",
            "WEB_ADMIN_SECURE_COOKIE=false",
            "WEB_ADMIN_ALLOW_PRODUCTION=false",
            "",
        ]
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(contents)
    print(f"Private configuration saved: {path}. Do not publish it.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-config", type=Path, metavar="FILE")
    args = parser.parse_args()
    if args.create_config:
        configure(args.create_config)
        return
    settings = AdminSettings()
    web.run_app(create_console(settings), host=settings.host, port=settings.port, access_log=None)


if __name__ == "__main__":
    main()
