#!/usr/bin/env python3
"""Read-only host metadata; no tokens, personal data or dump content is exported."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def collect(directory: Path) -> dict:
    disk = shutil.disk_usage(directory)
    data = {
        "collected_at": datetime.now(UTC).isoformat(),
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "load_1m": os.getloadavg()[0],
    }
    try:
        memory = {
            line.split(":")[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:"))
        }
        data["memory_total_bytes"] = memory["MemTotal"]
        data["memory_available_bytes"] = memory["MemAvailable"]
    except (OSError, KeyError, ValueError):
        pass
    try:
        files = list((directory / "backups").glob("setlist-*.dump"))
        if files:
            latest = max(files, key=lambda path: path.stat().st_mtime)
            stat = latest.stat()
            data.update(
                last_backup=latest.name,
                backup_bytes=stat.st_size,
                backup_age_seconds=max(0, int(datetime.now(UTC).timestamp() - stat.st_mtime)),
            )
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "setlist-sandbox-backup.timer"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        data["backup_timer"] = result.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        data["backup_timer"] = "unknown"
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("/opt/setlist_bot_codex"))
    args = parser.parse_args()
    report_dir = args.directory / ".admin-status"
    report_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    report_dir.chmod(0o755)
    fd, temporary = tempfile.mkstemp(prefix=".host-", dir=report_dir)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(collect(args.directory), output)
        os.chmod(temporary, 0o644)
        os.replace(temporary, report_dir / "host.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
