"""RU: Выпуск песочницы без живых сервисов. EN: Fake Docker verifies release ordering."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sandbox_release(tmp_path):
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    script = (ROOT / "scripts/update-sandbox-admin.sh").read_text()
    (app / "scripts/update-sandbox-admin.sh").write_text(
        script.replace("/opt/setlist_bot_sandbox", str(app))
    )
    (app / "scripts/backup-sandbox.sh").write_bytes(
        (ROOT / "scripts/backup-sandbox.sh").read_bytes()
    )
    (app / ".env").write_text("SANDBOX_BOT_TOKEN=1110437563:SYNTHETIC\n")
    (app / ".env.admin").write_text("WEB_ADMIN_USERNAME=unchanged\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "git").write_text(
        '#!/bin/sh\ncase "$1" in rev-parse) echo synthetic-revision;; '
        'status) printf "%s" "${DIRTY:-}";; *) exit 90;; esac\n'
    )
    (binary / "git").chmod(0o755)
    (binary / "docker").write_text("""#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as log: log.write(json.dumps(args)+'\\n')
assert 'compose.production.yaml' not in args and 'compose.yaml' not in args
if 'pg_dump' in args:
    assert args[-2:] == ['setlist_sandbox','-Fc']
    sys.stdout.buffer.write(b'PGDMP_sandbox_archive')
elif 'pg_restore' in args:
    assert '--list' in args and sys.stdin.buffer.read().startswith(b'PGDMP')
elif 'run' in args and 'migrate' in args and '--check' not in args:
    if os.environ.get('FAIL_STAGE')=='migration': raise SystemExit(1)
elif 'exec' in args and 'admin' in args:
    assert '/sandbox/healthz' in sys.stdin.read()
    if os.environ.get('FAIL_STAGE')=='health': raise SystemExit(1)
""")
    (binary / "docker").chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    return (
        app,
        calls,
        {
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "CALLS": str(calls),
        },
    )


def invoke(environment, mode="cloud", **extra):
    app, calls, env = environment
    result = subprocess.run(
        ["bash", str(app / "scripts/update-sandbox-admin.sh"), mode],
        cwd=app,
        env={**env, **extra},
        capture_output=True,
        text=True,
    )
    operations = (
        [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    )
    return result, operations


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("mode", ["cloud", "server"])
def test_sandbox_release_preserves_optional_production_access_and_poller_mode(
    sandbox_release, connected, mode
):
    app, _, _ = sandbox_release
    original = (app / ".env.admin").read_text()
    if connected:
        original += (
            'WEB_ADMIN_ALLOW_PRODUCTION="true"\n'
            "WEB_ADMIN_PRODUCTION_DATABASE_URL=postgresql+asyncpg://role:PRIVATE@host/setlist\n"
        )
        (app / ".env.admin").write_text(original)
    result, operations = invoke(sandbox_release, mode)
    assert result.returncode == 0, result.stderr
    assert (app / ".env.admin").read_text() == original
    assert "PRIVATE" not in result.stdout + result.stderr
    admin_start = next(args for args in operations if "up" in args and args[-1] == "admin")
    assert ("compose.admin.production-access.yaml" in admin_start) == connected
    bot_starts = [args for args in operations if "up" in args and args[-1] == "bot"]
    assert len(bot_starts) == (1 if mode == "server" else 0)
    stop = next(i for i, args in enumerate(operations) if "stop" in args)
    backup = next(i for i, args in enumerate(operations) if "pg_dump" in args)
    migrate = next(
        i for i, args in enumerate(operations) if "run" in args and "--check" not in args
    )
    health = next(i for i, args in enumerate(operations) if "exec" in args and "admin" in args)
    assert stop < backup < migrate < health
    assert not any("down" in args or "dropdb" in args for args in operations)


@pytest.mark.parametrize("stage", ["migration", "health"])
def test_failed_sandbox_release_never_starts_polling(sandbox_release, stage):
    result, operations = invoke(sandbox_release, "server", FAIL_STAGE=stage)
    assert result.returncode != 0
    assert not any("up" in args and args[-1] == "bot" for args in operations)


def test_sandbox_release_refuses_dirty_checkout_and_partial_admin_config(sandbox_release):
    result, operations = invoke(sandbox_release, DIRTY=" M source.py")
    assert result.returncode != 0 and not operations
    app, _, _ = sandbox_release
    (app / ".env.admin").write_text("WEB_ADMIN_ALLOW_PRODUCTION=true\n")
    result, operations = invoke(sandbox_release)
    assert result.returncode != 0 and not operations
