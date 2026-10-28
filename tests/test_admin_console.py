"""Actual isolated PostgreSQL databases, scoped browser sessions and mutation forms."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_admin import PASSWORD, extract, settings
from test_migrations import alembic

from setlist_bot.admin.console import create_console
from setlist_bot.application.groups import Groups
from setlist_bot.domain.entities import DeploymentIdentity, Group, User
from setlist_bot.infrastructure.database import PostgresUnitOfWork


@pytest_asyncio.fixture
async def second_engine(engine):
    name = "setlist_console_" + uuid4().hex[:8] + "_test"
    assert name.endswith("_test")
    control = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with control.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    other = create_async_engine(engine.url.set(database=name))
    try:
        alembic(other.url.render_as_string(hide_password=False), "upgrade", "head")
        yield other
    finally:
        await other.dispose()
        async with control.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}"'))
        await control.dispose()


async def console_login(browser, prefix):
    page = await browser.get(prefix + "/login")
    assert page.status == 200, await page.text()
    values = extract(await page.text(), prefix + "/login")
    values.update(username="admin", password=PASSWORD)
    response = await browser.post(prefix + "/login", data=values, allow_redirects=False)
    assert response.status == 303, await response.text()
    return response


async def test_console_databases_caches_sessions_and_forms_are_isolated(
    engine, second_engine, world, tmp_path
):
    async with PostgresUnitOfWork(second_engine) as uow:
        operator = User(telegram_id=100)
        uow.store.add(operator)
        await uow.store.flush()
        production_group = await Groups(uow.store, operator).create("Другой контур", -999)
        production_id = production_group.id
    reports = {}
    for name in ("sandbox", "production"):
        path = tmp_path / (name + ".json")
        path.write_text(
            json.dumps(
                {
                    "collected_at": datetime.now(UTC).isoformat(),
                    "last_backup": name + "-only.dump",
                    "backup_bytes": 1,
                    "backup_age_seconds": 0,
                    "disk_free_bytes": 100,
                    "disk_total_bytes": 200,
                }
            )
        )
        reports[name] = str(path)
    config = settings(engine).model_copy(
        update={
            "allow_production": True,
            "production_database_url": SecretStr(
                second_engine.url.render_as_string(hide_password=False)
            ),
            "production_read_only": False,
            "host_report": reports["sandbox"],
            "production_host_report": reports["production"],
        }
    )
    console = create_console(config, {"sandbox": engine, "production": second_engine})
    async with TestClient(TestServer(console), cookie_jar=CookieJar(unsafe=True)) as browser:
        browser.session.cookie_jar.update_cookies(
            {"setlist_admin": "legacy-root-token", "setlist_admin_login": "legacy-root-csrf"}
        )
        redirect = await browser.get("/login", allow_redirects=False)
        assert redirect.status == 303 and redirect.headers["Location"] == "/sandbox/login"
        assert (await browser.post("/actions/group_reminders/anything")).status == 409
        response = await console_login(browser, "/sandbox")
        assert any("Path=/sandbox" in x for x in response.headers.getall("Set-Cookie"))
        page = await browser.get("/sandbox/")
        body = await page.text()
        assert 'href="/sandbox/users"' in body and 'href="/_contour/production"' in body
        assert 'src="/sandbox/static/admin.js"' in body
        assert (await browser.get("/sandbox/static/admin.js")).status == 200
        assert (await (await browser.get("/sandbox/api/overview")).json())["users"] == 3
        assert (await browser.get("/production/", allow_redirects=False)).headers[
            "Location"
        ] == "/production/login"
        await console_login(browser, "/production")
        for name, other in (("sandbox", "production"), ("production", "sandbox")):
            monitoring = await (await browser.get("/" + name + "/runtime")).text()
            assert name + "-only.dump" in monitoring and other + "-only.dump" not in monitoring
            label = (
                "Последняя копия основной базы"
                if name == "production"
                else "Последняя копия песочницы"
            )
            assert label in monitoring
        assert (await (await browser.get("/production/api/overview")).json())["users"] == 1
        page = await browser.get(f"/sandbox/groups/{world.group.id}")
        path = f"/sandbox/actions/group_reminders/{world.group.id}"
        proof = extract(await page.text(), path)
        # A valid sandbox form cannot change production, even after opening that contour.
        wrong_path = f"/production/actions/group_reminders/{production_id}"
        assert (await browser.post(wrong_path, data=proof)).status == 403
        assert (await browser.post(path, data=proof, allow_redirects=False)).status == 303
        async with PostgresUnitOfWork(second_engine) as uow:
            assert (await uow.store.get(Group, production_id)).reminders_enabled
        async with PostgresUnitOfWork(engine) as uow:
            assert not (await uow.store.get(Group, world.group.id)).reminders_enabled
        # Logout only clears the cookie of the current contour.
        page = await browser.get("/sandbox/")
        path = "/sandbox/actions/logout/session"
        proof = extract(await page.text(), path)
        response = await browser.post(path, data=proof, allow_redirects=False)
        assert response.status == 303
        assert (await browser.get("/production/", allow_redirects=False)).status == 200
        assert (await browser.get("/sandbox/", allow_redirects=False)).status == 303


async def test_missing_production_never_falls_back_to_sandbox(engine):
    async with TestClient(
        TestServer(create_console(settings(engine), {"sandbox": engine})),
        cookie_jar=CookieJar(unsafe=True),
    ) as browser:
        assert (await browser.get("/production/")).status == 503
        assert (await browser.get("/_contour/production")).status == 503
        await console_login(browser, "/sandbox")
        body = await (await browser.get("/sandbox/")).text()
        assert "Основной ещё не подключён" in body


async def test_read_only_is_enforced_on_server(client, engine, world):
    # The mode is checked independently of whether the browser shows a submit button.
    from test_admin import login

    from setlist_bot.admin.app import STATE

    await login(client)
    page = await client.get(f"/groups/{world.group.id}")
    path = f"/actions/group_reminders/{world.group.id}"
    proof = extract(await page.text(), path)
    client.app[STATE].settings.read_only = True
    assert (await client.post(path, data=proof)).status == 403
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Group, world.group.id)).reminders_enabled


@pytest_asyncio.fixture
async def client(engine):
    from setlist_bot.admin.app import create_app

    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        yield browser


def test_console_refuses_silent_database_alias_and_unapproved_production(engine):
    config = settings(engine)
    config.production_database_url = config.database_url
    with pytest.raises(ValueError, match="requires"):
        create_console(config)
    config.allow_production = True
    with pytest.raises(ValueError, match="different databases"):
        create_console(config)


async def test_console_rejects_a_database_bound_to_another_bot(engine):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=7430933141, environment="production"))
    console = create_console(settings(engine), {"sandbox": engine})
    async with TestClient(TestServer(console)) as browser:
        response = await browser.get("/sandbox/login")
        assert response.status == 409 and "другому боту" in await response.text()
