"""Exercise release failure ordering with fake Docker, never a production database."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release_environment(tmp_path):
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    original = (ROOT / "scripts/release-production.sh").read_text()
    (app / "scripts/release-production.sh").write_text(
        original.replace("/opt/setlist_bot_codex", str(app))
    )
    (app / "scripts/backup-server.sh").write_bytes((ROOT / "scripts/backup-server.sh").read_bytes())
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "git").write_text(
        '#!/bin/sh\ncase "$1" in branch) echo main;; '
        "rev-parse) echo synthetic-revision;; status) :;; *) exit 90;; esac\n"
    )
    (binary / "git").chmod(0o755)
    (binary / "docker").write_text("""#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as log: log.write(json.dumps(args)+'\\n')
if 'pg_dump' in args:
    sys.stdout.buffer.write(b'PGDMP_test_archive')
elif 'pg_restore' in args:
    content=sys.stdin.buffer.read()
    assert content.startswith(b'PGDMP')
    if '--list' not in args:
        target=args[args.index('-d')+1]
        assert target.startswith('setlist_admin_release_') and target.endswith('_test')
elif 'exec' in args and 'bot' in args and 'python' in ' '.join(args):
    print(os.environ.get('FAKE_ID','7430933141'))
elif 'run' in args and '.venv/bin/python' in args:
    target=next(x.split('=',1)[1] for x in args if x.startswith('RELEASE_CHECK_DATABASE='))
    assert target.endswith('_test') and target!='setlist'
    assert "assert original.database == 'setlist'" in sys.stdin.read()
    if os.environ.get('FAIL_RELEASE')=='rehearsal': raise SystemExit(1)
elif 'run' in args and 'migrate' in args and '--check' not in args:
    if os.environ.get('FAIL_RELEASE')=='migration': raise SystemExit(1)
elif 'createdb' in args or 'dropdb' in args:
    assert args[-1].startswith('setlist_admin_release_') and args[-1].endswith('_test')
""")
    (binary / "docker").chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "CALLS": str(calls),
        "SETLIST_APP_DIR": str(app),
    }
    return app, calls, env


def invoke(environment, **extra):
    app, calls, env = environment
    result = subprocess.run(
        ["bash", str(app / "scripts/release-production.sh")],
        cwd=app,
        env={**env, **extra},
        capture_output=True,
        text=True,
    )
    operations = (
        [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    )
    return result, operations


def test_release_rehearses_before_shutdown_and_never_restores_into_live_db(release_environment):
    result, operations = invoke(release_environment)
    assert result.returncode == 0, result.stderr
    rehearsal = next(
        i for i, args in enumerate(operations) if "RELEASE_CHECK_DATABASE=" in " ".join(args)
    )
    stop = next(i for i, args in enumerate(operations) if "stop" in args)
    start = next(i for i, args in enumerate(operations) if "up" in args)
    assert rehearsal < stop < start
    assert sum("pg_dump" in args for args in operations) == 2
    assert sum("pg_restore" in args and "-d" in args for args in operations) == 1


def test_failed_rehearsal_preserves_running_production_and_removes_only_temp_db(
    release_environment,
):
    result, operations = invoke(release_environment, FAIL_RELEASE="rehearsal")
    assert result.returncode != 0
    assert not any("stop" in args or "up" in args for args in operations)
    assert sum("dropdb" in args for args in operations) == 1


def test_failed_live_migration_does_not_start_unchecked_version(release_environment):
    result, operations = invoke(release_environment, FAIL_RELEASE="migration")
    assert result.returncode != 0
    assert any("stop" in args for args in operations)
    assert not any("up" in args for args in operations)
    assert "Release stopped after bot shutdown" in result.stdout


def test_wrong_bot_is_rejected_before_build_backup_or_stop(release_environment):
    result, operations = invoke(release_environment, FAKE_ID="1110437563")
    assert result.returncode != 0
    assert not any(
        any(x in args for x in ("build", "pg_dump", "stop", "run")) for args in operations
    )
