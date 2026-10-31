import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict

import pytest
from aiohttp.test_utils import TestClient, TestServer

from setlist_bot.application.calendars import Calendars, token_hash
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.calendar.app import CACHE, create_app
from setlist_bot.calendar.settings import CalendarSettings, public_base_url
from setlist_bot.domain.entities import (
    CalendarSubscription,
    DeploymentIdentity,
    Group,
    Membership,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def issue(engine, user, group_id=None):
    async with PostgresUnitOfWork(engine) as uow:
        row, token = await Calendars(uow.store, user).create(group_id)
        return row.id, token, row.version


@asynccontextmanager
async def client_for(engine):
    async with PostgresUnitOfWork(engine) as uow:
        if not await uow.store.find(DeploymentIdentity):
            uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    config = CalendarSettings(
        database_url=engine.url.render_as_string(hide_password=False),
        expected_bot_id=1110437563,
        environment="sandbox",
        bot_username="botToPract696_bot",
    )
    async with TestClient(TestServer(create_app(config, engine))) as client:
        yield client


async def test_feed_only_contains_active_memberships_and_no_personal_or_account_data(engine, world):
    _, token, _ = await issue(engine, world.member)
    async with PostgresUnitOfWork(engine) as uow:
        await ConcertDetails(uow.store, world.admin).update(
            world.group.id, world.concert.id, "concert_info", "Начало: 20:00\nКонец: 21:40"
        )
    async with client_for(engine) as client:
        response = await client.get(f"/calendar/{token}.ics")
        body = (await response.text()).replace("\r\n ", "")
        assert response.status == 200 and response.content_type == "text/calendar"
        assert (
            body.count("BEGIN:VEVENT") == 2 and "Коллектив А" in body and "Коллектив Б" not in body
        )
        assert "Начало: 20:00\\nКонец: 21:40" in body
        assert world.song.title not in body and "telegram_id" not in body and "owner_id" not in body
        assert "Cache-Control" in response.headers and "ETag" in response.headers
        head = await client.head(f"/calendar/{token}.ics")
        assert head.status == 200 and await head.read() == b""
        assert (await client.post(f"/calendar/{token}.ics")).status == 405
        assert (await client.get("/admin/login")).status == 404


async def test_group_subscription_scope_and_member_can_subscribe_but_cannot_edit(engine, world):
    _, token, _ = await issue(engine, world.admin, world.other.id)
    async with client_for(engine) as client:
        response = await client.get(f"/calendar/{token}.ics")
        body = await response.text()
        assert "Коллектив Б" in body and "Коллектив А" not in body
    await issue(engine, world.member, world.group.id)
    with pytest.raises(AppError):
        await issue(engine, world.member, world.other.id)
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="admin_only"):
            await ConcertDetails(uow.store, world.member).update(
                world.group.id, world.concert.id, "concert_title", "Не разрешено"
            )


async def test_etag_refreshes_metadata_and_revoked_access_never_gets_cached_304(engine, world):
    _, token, _ = await issue(engine, world.member)
    path = f"/calendar/{token}.ics"
    async with client_for(engine) as client:
        response = await client.get(path)
        etag = response.headers["ETag"]
        assert (await client.get(path, headers={"If-None-Match": etag})).status == 304
        async with PostgresUnitOfWork(engine) as uow:
            await ConcertDetails(uow.store, world.admin).update(
                world.group.id, world.concert.id, "concert_city", "Казань"
            )
        changed = await client.get(path, headers={"If-None-Match": etag})
        assert changed.status == 200 and changed.headers["ETag"] != etag
        assert "Казань" in await changed.text()
        etag = changed.headers["ETag"]
        async with PostgresUnitOfWork(engine) as uow:
            row = (await uow.store.find(Membership, user_id=world.member.id))[0]
            row.status = "pending"
        denied = await client.get(path, headers={"If-None-Match": etag})
        assert denied.status == 200 and "BEGIN:VEVENT" not in await denied.text()
        assert len(client.server.app[CACHE]) >= 1


async def test_rotation_disable_and_double_creation_do_not_store_or_return_old_token(engine, world):
    identifier, token, version = await issue(engine, world.admin)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(CalendarSubscription, identifier)
        assert row.token_hash == token_hash(token)
        assert token not in json.dumps(asdict(row), default=str)
        again, repeated_token = await Calendars(uow.store, world.admin).create(None)
        assert again.id == identifier and repeated_token is None
        with pytest.raises(AppError):
            await Calendars(uow.store, world.stranger).change(identifier, version, "disable")
    async with client_for(engine) as client:
        async with PostgresUnitOfWork(engine) as uow:
            _, replacement = await Calendars(uow.store, world.admin).change(
                identifier, version, "rotate"
            )
        assert (await client.get(f"/calendar/{token}.ics")).status == 404
        assert (await client.get(f"/calendar/{replacement}.ics")).status == 200
        async with PostgresUnitOfWork(engine) as uow:
            with pytest.raises(AppError):
                await Calendars(uow.store, world.admin).change(identifier, version, "rotate")
            await Calendars(uow.store, world.admin).change(identifier, version + 1, "disable")
        assert (await client.get(f"/calendar/{replacement}.ics")).status == 404
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(CalendarSubscription)) == 1


async def test_duplicate_concurrent_creations_return_exactly_one_new_link(engine, world):
    results = await asyncio.gather(*(issue(engine, world.admin) for _ in range(5)))
    assert len({row[0] for row in results}) == 1
    assert sum(row[1] is not None for row in results) == 1


async def test_group_trash_immediately_disappears_and_logs_do_not_contain_secret_urls(
    engine, world, caplog
):
    _, token, _ = await issue(engine, world.member, world.group.id)
    async with client_for(engine) as client:
        assert (await client.get(f"/calendar/{token}.ics")).status == 200
        async with PostgresUnitOfWork(engine) as uow:
            (await uow.store.get(Group, world.group.id)).deleted_at = now()
        response = await client.get(f"/calendar/{token}.ics")
        assert "BEGIN:VEVENT" not in await response.text()
        assert (await client.get("/calendar/invalid.ics")).status == 404
    assert token not in caplog.text


async def test_errors_do_not_log_token_or_private_exception_message(
    engine, world, caplog, monkeypatch
):
    from setlist_bot.infrastructure.calendar_queries import CalendarQueries

    _, token, _ = await issue(engine, world.admin)

    async def fail(*args):
        raise ValueError("private: " + token)

    monkeypatch.setattr(CalendarQueries, "read", fail)
    async with client_for(engine) as client:
        response = await client.get(f"/calendar/{token}.ics")
        assert response.status == 503 and token not in await response.text()
    assert token not in caplog.text and "ValueError" in caplog.text


async def test_calendar_migration_refuses_silent_loss_of_active_links(engine, world, database_url):
    from test_migrations import alembic

    identifier, _, _ = await issue(engine, world.admin)
    refused = alembic(database_url, "downgrade", "a82cd901be45", check=False)
    assert refused.returncode != 0 and "active calendar subscriptions" in refused.stderr
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(CalendarSubscription, identifier)
        row.enabled = False
    alembic(database_url, "downgrade", "a82cd901be45")
    alembic(database_url, "upgrade", "head")
    alembic(database_url, "check")


@pytest.mark.parametrize(
    "value",
    [
        "http://example.org",
        "https://user:password@example.org",
        "https://example.org/path",
        "https://example.org?secret=a",
    ],
)
def test_calendar_origin_does_not_accept_credentials_or_paths(value):
    with pytest.raises(ValueError):
        public_base_url(value)
