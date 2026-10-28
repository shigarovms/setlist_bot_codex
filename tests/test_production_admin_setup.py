"""Test production connection helpers only against isolated PostgreSQL *_test data."""

import importlib.util
import os
import stat
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from setlist_bot.application.administration import Administration
from setlist_bot.application.support import Support
from setlist_bot.domain.entities import Group, Membership, Outbox, PersonalDraft
from setlist_bot.infrastructure.admin_queries import AdminQueries
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.runtime import SCHEMA_REVISION

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "production_admin_setup", ROOT / "scripts/connect-production-admin.py"
)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def test_configuration_preserves_existing_login_and_replaces_only_production_fields():
    original = (
        "WEB_ADMIN_USERNAME=operator\nWEB_ADMIN_PASSWORD_HASH=synthetic-hash\n"
        "WEB_ADMIN_SECRET=synthetic-secret\n"
        "WEB_ADMIN_DATABASE_URL=postgresql+asyncpg://u:p@db/setlist_sandbox\n"
        "# preserved comment\nWEB_ADMIN_ALLOW_PRODUCTION=false\n"
    )
    changed = setup.updated_configuration(original, "a" * 64, True)
    values = setup.configured_values(changed)
    assert values["WEB_ADMIN_USERNAME"] == "operator"
    assert values["WEB_ADMIN_SECRET"] == "synthetic-secret"
    assert values["WEB_ADMIN_PASSWORD_HASH"] == "synthetic-hash"
    assert values["WEB_ADMIN_DATABASE_URL"].endswith("/setlist_sandbox")
    assert values["WEB_ADMIN_PRODUCTION_DATABASE_URL"].endswith(
        "@setlist_bot_codex-db-1:5432/setlist"
    )
    assert values["WEB_ADMIN_PRODUCTION_READ_ONLY"] == "false"
    assert changed.count("WEB_ADMIN_ALLOW_PRODUCTION=") == 1 and "# preserved comment" in changed
    assert setup.SCHEMA == SCHEMA_REVISION


def test_helpers_refuse_wrong_checkout_before_any_docker_calls(tmp_path):
    for command in (
        [os.sys.executable, str(ROOT / "scripts/connect-production-admin.py")],
        ["bash", str(ROOT / "scripts/release-production.sh")],
    ):
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode != 0
        assert "Run only from" in result.stdout + result.stderr


def test_grants_reject_injected_identifiers_and_do_not_disclose_failed_sql(tmp_path):
    with pytest.raises(ValueError):
        setup.grants("role; DROP DATABASE setlist", "safe_test", True)
    with pytest.raises(ValueError):
        setup.grants("safe_role", "setlist;bad", True)
    password = "synthetic-password-not-for-logs"
    with pytest.raises(RuntimeError) as caught:
        setup.run(
            [os.sys.executable, "-c", "import sys; print(sys.stdin.read()); sys.exit(1)"],
            cwd=tmp_path,
            data=password,
        )
    assert password not in str(caught.value)


@pytest.mark.parametrize("actions", [False, True])
async def test_dedicated_role_can_use_console_without_reading_drafts_or_deleting_catalog(
    engine, world, actions
):
    database = engine.url.database
    assert database.endswith("_test")
    role = "setlist_admin_" + uuid4().hex[:12]
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        await Support(uow.store, world.member).submit("Test request")
        ticket = (await uow.store.find(Outbox, support_kind="request"))[0].id
        uow.store.add(
            PersonalDraft(
                group_id=world.group.id,
                concert_id=world.concert.id,
                owner_id=world.member.id,
                content={"private": "must stay private"},
            )
        )
        if actions:
            from setlist_bot.application.trash import Trash

            trash = Trash(uow.store, world.admin)
            await trash.delete_concert(
                world.group.id,
                world.concert.id,
                await trash.concert_fingerprint(world.group.id, world.concert.id),
            )
            await trash.delete_group(world.group.id, await trash.group_fingerprint(world.group.id))
    async with engine.begin() as connection:
        await connection.execute(
            text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
        )
        for statement in setup.grants(role, database, actions).split(";"):
            if statement.strip():
                await connection.execute(text(statement))
    limited = create_async_engine(engine.url, connect_args={"server_settings": {"role": role}})
    try:
        queries = AdminQueries(limited)
        overview = await queries.overview()
        assert overview["users"] == 3 and overview["drafts"] == 1
        assert (await queries.listing("groups")) and (await queries.runtime()) == []
        for sql in (
            "SELECT content FROM personal_drafts",
            "DELETE FROM songs",
            "DELETE FROM admin_audit",
            "UPDATE admin_audit SET actor='forged'",
            "SELECT content FROM export_files",
            "CREATE TABLE forbidden(id int)",
        ):
            with pytest.raises(ProgrammingError):
                async with limited.begin() as connection:
                    await connection.execute(text(sql))
        # Auth tables stay writable for login/audit; domain changes are independently
        # constrained by PostgreSQL in addition to the read-only application routes.
        if not actions:
            with pytest.raises(ProgrammingError):
                async with limited.begin() as connection:
                    await connection.execute(text("UPDATE groups SET reminders_enabled=false"))
        else:
            async with PostgresUnitOfWork(limited) as uow:
                await Administration(uow.store, "operator").execute(
                    "restore_group", world.group.id, {"epoch": "1", "confirmed": "yes"}, uuid4().hex
                )
                await Administration(uow.store, "operator").execute(
                    "restore_concert",
                    world.concert.id,
                    {"epoch": "1", "confirmed": "yes"},
                    uuid4().hex,
                )
                await Administration(uow.store, "operator").execute(
                    "support_close", ticket, {"expected_version": "0"}, uuid4().hex
                )
                await Administration(uow.store, "operator").execute(
                    "support_reply",
                    ticket,
                    {"expected_version": "1", "message": "Ответ из веба"},
                    uuid4().hex,
                )
            async with PostgresUnitOfWork(limited) as uow:
                await Administration(uow.store, "operator").execute(
                    "group_reminders", world.group.id, {"enabled": "no"}, uuid4().hex
                )
                member = (
                    await uow.store.find(
                        Membership, group_id=world.group.id, user_id=world.member.id
                    )
                )[0]
                await Administration(uow.store, "operator").execute(
                    "group_owner",
                    world.group.id,
                    {
                        "confirmation": world.group.name,
                        "expected_owner": str(world.admin.id),
                        "member_id": str(member.id),
                    },
                    uuid4().hex,
                )
            async with PostgresUnitOfWork(engine) as uow:
                group = await uow.store.get(Group, world.group.id)
                assert not group.reminders_enabled and group.owner_id == world.member.id
            async with engine.begin() as connection:
                for statement in setup.grants(role, database, False).split(";"):
                    if statement.strip():
                        await connection.execute(text(statement))
            with pytest.raises(ProgrammingError):
                async with limited.begin() as connection:
                    await connection.execute(text("UPDATE groups SET reminders_enabled=true"))
    finally:
        await limited.dispose()
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{role}"'))
            await connection.execute(text(f'DROP ROLE "{role}"'))


def test_connector_writes_private_config_and_never_outputs_database_password(
    tmp_path, monkeypatch, capsys
):
    app = tmp_path / "sandbox"
    (app / "scripts").mkdir(parents=True)
    config = app / ".env.admin"
    original = (
        "WEB_ADMIN_USERNAME=operator\nWEB_ADMIN_PASSWORD_HASH=synthetic\nWEB_ADMIN_SECRET=keep\n"
    )
    config.write_text(original)
    monkeypatch.setattr(setup, "SANDBOX", app)
    monkeypatch.setattr(setup, "__file__", str(app / "scripts/connect-production-admin.py"))
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.chdir(app)
    monkeypatch.setattr(setup.sys, "argv", ["connect-production-admin.py", "--enable-actions"])
    queries = []

    def fake_sql(statement):
        queries.append(statement)
        if "SELECT version_num" in statement:
            return SCHEMA_REVISION
        if "SELECT bot_id" in statement:
            return "7430933141:production"
        if "SELECT count(*) FROM runtime_status" in statement:
            return "1"
        if "SELECT EXISTS" in statement:
            return "f"
        return ""

    commands = []
    monkeypatch.setattr(setup, "sql", fake_sql)
    monkeypatch.setattr(setup, "run", lambda command, **kwargs: commands.append(command) or "")
    monkeypatch.setattr(setup, "configure_host_status", lambda: None)
    monkeypatch.setattr(setup, "verify_console", lambda: None)
    setup.main()
    contents = config.read_text()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    backup = next(app.glob(".env.admin.before-production-*"))
    assert backup.read_text() == original and stat.S_IMODE(backup.stat().st_mode) == 0o600
    url = setup.urlsplit(setup.configured_values(contents)["WEB_ADMIN_PRODUCTION_DATABASE_URL"])
    output = capsys.readouterr().out
    assert url.password not in output and url.password not in str(commands)
    assert any("CREATE ROLE" in query for query in queries)
    assert "WEB_ADMIN_USERNAME=operator" in contents
    assert all("bot" not in command for command in commands)


def test_host_report_uses_the_timer_and_backup_of_its_own_contour(tmp_path, monkeypatch):
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "admin_host_status", ROOT / "scripts/admin-host-status.py"
    )
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    commands = []
    monkeypatch.setattr(
        report.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or SimpleNamespace(stdout="active"),
    )
    for folder, timer in (
        ("setlist_bot_codex", "setlist-backup.timer"),
        ("setlist_bot_sandbox", "setlist-sandbox-backup.timer"),
    ):
        directory = tmp_path / folder
        (directory / "backups").mkdir(parents=True)
        (directory / "backups" / ("setlist-" + folder + ".dump")).write_bytes(
            b"private dump contents"
        )
        data = report.collect(directory)
        assert data["last_backup"] == "setlist-" + folder + ".dump" and commands[-1][-1] == timer
        assert "private dump contents" not in str(data)


def test_failed_role_setup_retains_private_recovery_file_without_changing_live_config(
    tmp_path, monkeypatch
):
    app = tmp_path / "sandbox"
    (app / "scripts").mkdir(parents=True)
    config = app / ".env.admin"
    original = "WEB_ADMIN_USERNAME=operator\nWEB_ADMIN_SECRET=keep\n"
    config.write_text(original)
    config.chmod(0o600)
    monkeypatch.setattr(setup, "SANDBOX", app)
    monkeypatch.setattr(setup, "__file__", str(app / "scripts/connect-production-admin.py"))
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.chdir(app)
    monkeypatch.setattr(setup.sys, "argv", ["connect-production-admin.py"])

    def fake_sql(statement):
        if "SELECT version_num" in statement:
            return SCHEMA_REVISION
        if "SELECT bot_id" in statement:
            return "7430933141:production"
        if "SELECT EXISTS" in statement:
            return "f"
        raise RuntimeError("synthetic database interruption")

    monkeypatch.setattr(setup, "sql", fake_sql)
    with pytest.raises(RuntimeError):
        setup.main()
    pending = app / ".env.admin.production-pending"
    assert config.read_text() == original and stat.S_IMODE(pending.stat().st_mode) == 0o600
    password = setup.saved_password(pending.read_text())
    assert password and len(password) == 64
