import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup-server.sh"


@pytest.fixture
def backup_environment(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    backups = app / "backups"
    backups.mkdir()
    old = backups / "setlist-old.dump"
    unrelated = backups / "manual.dump"
    for path in (old, unrelated):
        path.write_bytes(b"old archive")
        os.utime(path, (time.time() - 20 * 86400,) * 2)
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("""#!/usr/bin/env python3
import os
import sys
if 'pg_dump' in sys.argv:
    if os.environ.get('FAKE_BACKUP_FAILURE') == 'dump':
        sys.stdout.buffer.write(b'partial')
        raise SystemExit(1)
    sys.stdout.buffer.write(b'PGDMP_valid_archive')
elif 'pg_restore' in sys.argv:
    data = sys.stdin.buffer.read()
    if os.environ.get('FAKE_BACKUP_FAILURE') == 'validation' or not data.startswith(b'PGDMP'):
        raise SystemExit(1)
else:
    raise SystemExit(99)
""")
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "SETLIST_APP_DIR": str(app),
    }
    return app, backups, old, unrelated, environment


def test_backup_is_atomic_private_and_rotates_only_its_own_old_archives(backup_environment):
    app, backups, old, unrelated, environment = backup_environment
    result = subprocess.run(["bash", str(SCRIPT)], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    generated = list(backups.glob("setlist-*.dump"))
    assert len(generated) == 1 and generated[0].read_bytes() == b"PGDMP_valid_archive"
    assert stat.S_IMODE(generated[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    assert not old.exists() and unrelated.exists()
    assert not list(backups.glob(".backup-*"))


@pytest.mark.parametrize("failure", ["dump", "validation"])
def test_failed_backup_keeps_previous_archives_and_removes_partial_file(
    backup_environment, failure
):
    app, backups, old, unrelated, environment = backup_environment
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**environment, "FAKE_BACKUP_FAILURE": failure},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert old.read_bytes() == b"old archive" and unrelated.exists()
    assert set(path.name for path in backups.iterdir()) == {"setlist-old.dump", "manual.dump"}
