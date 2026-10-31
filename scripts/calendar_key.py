"""RU: Постоянный ключ вне БД. EN: Preserve keys; never silently replace a missing key."""

import base64
import os
import re
import secrets
import subprocess
from collections.abc import Callable
from pathlib import Path


def existing_ciphertexts() -> bool:
    def query(statement: str) -> str:
        result = subprocess.run(
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
            input=statement,
            capture_output=True,
            text=True,
            check=True,
        )
        answer = result.stdout.strip()
        if answer not in ("t", "f"):
            raise RuntimeError("Cannot verify calendar key state")
        return answer

    if (
        query(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='calendar_subscriptions' AND column_name='token_ciphertext');"
        )
        == "f"
    ):
        return False
    return (
        query("SELECT EXISTS(SELECT 1 FROM calendar_subscriptions WHERE token_ciphertext <> '');")
        == "t"
    )


def ensure_key(root: Path, check_existing: Callable[[], bool] = existing_ciphertexts) -> None:
    path = root / ".env.calendar-key"
    if path.is_file():
        content = path.read_text().strip()
        match = re.fullmatch(r"CALENDAR_LINK_KEY=([A-Za-z0-9_-]{43}=)", content)
        if not match or len(base64.b64decode(match[1], altchars=b"-_", validate=True)) != 32:
            raise RuntimeError("Invalid existing calendar key; restore its private backup")
        os.chmod(path, 0o600)
        return
    if check_existing():
        raise RuntimeError(
            "Calendar key is missing while encrypted links exist; restore the key backup"
        )
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("CALENDAR_LINK_KEY=" + key + "\n")
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    try:
        ensure_key(Path.cwd())
        print("Calendar encryption key ready; existing key preserved.")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
        raise SystemExit(
            "Calendar key setup stopped; preserve/restore .env.calendar-key. No secrets printed."
        ) from None
