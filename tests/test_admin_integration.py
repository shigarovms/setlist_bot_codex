"""Cross-interface behavior and deployment safety; no real Telegram requests."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text

# Reuse HTTP fixtures with the same isolated _test database.
from test_admin import extract, login, settings
from test_telegram import FakeTelegram

from setlist_bot.admin.app import create_app
from setlist_bot.application.administration import Administration
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.support import Support
from setlist_bot.domain.entities import (
    AdminAudit,
    DeploymentIdentity,
    Group,
    Outbox,
    PersonalDraft,
    RuntimeStatus,
    SupportCase,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.runtime import (
    SCHEMA_REVISION,
    BotInstance,
    SchemaMismatch,
    check_schema,
    identity_details,
)
from setlist_bot.infrastructure.telemetry import Monitor
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.session import VerifiedSession
from setlist_bot.transport.telegram import Processor, send_response


@pytest_asyncio.fixture
async def client(engine):
    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        yield browser


async def test_schema_mismatch_is_explicit_and_prevents_start(engine):
    assert await check_schema(engine) == SCHEMA_REVISION
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE alembic_version SET version_num='older'"))
    try:
        with pytest.raises(SchemaMismatch, match=SCHEMA_REVISION):
            await check_schema(engine)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE alembic_version SET version_num=:version"),
                {"version": SCHEMA_REVISION},
            )


async def test_runtime_lock_rejects_duplicate_and_releases_for_handover(engine):
    async with BotInstance(engine, 1110437563, "sandbox") as active:
        await active.heartbeat()
        with pytest.raises(RuntimeError, match="второй polling"):
            async with BotInstance(engine, 1110437563, "sandbox"):
                pytest.fail("A duplicate instance acquired the lock")
    async with BotInstance(engine, 1110437563, "sandbox"):
        pass
    with pytest.raises(RuntimeError, match="другому боту"):
        async with BotInstance(engine, 7430933141, "production"):
            pass
    async with PostgresUnitOfWork(engine) as uow:
        identity = (await uow.store.find(DeploymentIdentity))[0]
        assert identity.bot_id == 1110437563 and identity.environment == "sandbox"


async def test_watchdog_never_reconnects_a_lost_lock(engine):
    async with BotInstance(engine, 1110437563, "sandbox") as instance:
        await instance.connection.invalidate()
        with pytest.raises(RuntimeError, match="Потеряно соединение"):
            await instance.heartbeat()


async def test_runtime_identity_and_actual_api_success_survive_heartbeat(engine):
    identity = identity_details(1110437563, "unit_bot", "sandbox", "cloud-over-ssh")
    monitor = Monitor(identity)
    session = VerifiedSession(monitor)
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        with patch(
            "aiogram.client.session.aiohttp.AiohttpSession.make_request",
            new=AsyncMock(return_value=[]),
        ):
            assert await session.make_request(bot, GetUpdates()) == []
        success = monitor.components["telegram_api"]["last_success_at"]
        failure = TelegramNetworkError(method=GetUpdates(), message="secret synthetic detail")
        with patch(
            "aiogram.client.session.aiohttp.AiohttpSession.make_request",
            new=AsyncMock(side_effect=failure),
        ):
            with pytest.raises(TelegramNetworkError):
                await session.make_request(bot, GetUpdates())
        monitor.set("bot", "process_running")
        await monitor.flush(engine)
    async with PostgresUnitOfWork(engine) as uow:
        api = (await uow.store.find(RuntimeStatus, component="telegram_api"))[0]
        process = (await uow.store.find(RuntimeStatus, component="bot"))[0]
        assert api.last_success_at == success and api.last_error_at > success
        assert api.last_error == "TelegramNetworkError" and "secret" not in repr(api)
        assert process.details["bot_id"] == 1110437563
        assert process.details["instance_id"] and process.details["schema"] == SCHEMA_REVISION


async def test_telegram_reply_invalidates_stale_web_reply_and_shows_actor(client, engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        await Support(uow.store, world.member).submit("Нужна помощь")
        ticket = (await uow.store.find(Outbox, support_kind="request"))[0].id
    await login(client)
    page = await client.get(f"/support/{ticket}")
    path = f"/actions/support_reply/{ticket}"
    stale = extract(await page.text(), path)
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).reply_to_request(ticket, "Ответ из Telegram")
    stale["message"] = "Устаревший второй ответ"
    assert (await client.post(path, data=stale)).status == 409
    page = await client.get(f"/support/{ticket}")
    body = await page.text()
    assert "tg://user?id=1" in body and "Ответ из Telegram" in body
    fresh = extract(body, path)
    fresh["message"] = "Уточнение через веб"
    fresh["close"] = "yes"
    assert (await client.post(path, data=fresh, allow_redirects=False)).status == 303
    async with PostgresUnitOfWork(engine) as uow:
        replies = await uow.store.find(Outbox, support_request_id=ticket)
        assert len(replies) == 2 and {r.source for r in replies} == {"web", "telegram"}
        case = (await uow.store.find(SupportCase, request_id=ticket))[0]
        assert case.version == 2 and case.status == "closed" and case.last_reply_by == "admin"
        logs = await uow.store.find(AdminAudit, action="support_reply")
        assert len(logs) == 2 and {r.source for r in logs} == {"web", "telegram"}


@pytest.mark.parametrize("language", ["ru", "en"])
async def test_queued_refresh_edits_shared_card_preserves_reply_binding_and_private_draft(
    engine, world, language
):
    processor, fake = Processor(engine), FakeTelegram()
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(Group, world.group.id)).language = language
        await Setlists(uow.store, world.admin).edit_songs(
            world.group.id, world.concert.id, 0, ["Комета"]
        )
    shown = await processor.process(1, 1, -100, f"use_concert {world.concert.id}", private=False)
    assert shown.menu_card_id is not None
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=fake) as bot:
        messages = await send_response(bot, -100, shown)
        await processor.remember_response(-100, shown, messages)
        original_ids = [m.message_id for m in messages]
        async with PostgresUnitOfWork(engine) as uow:
            await ConcertDetails(uow.store, world.admin).update(
                world.group.id, world.concert.id, "concert_city", "Москва"
            )
            jobs = await uow.store.find(Outbox, support_kind="refresh")
            assert len(jobs) == 1 and jobs[0].payload["card_id"] == str(shown.menu_card_id)
            # Two changes before delivery coalesce into one job, rendered from current data.
            await ConcertDetails(uow.store, world.admin).update(
                world.group.id, world.concert.id, "concert_info", "Уточнение"
            )
            assert len(await uow.store.find(Outbox, support_kind="refresh")) == 1
        await deliver_one_outbox(bot, engine)
        assert fake.edits and fake.edits[-1].message_id in original_ids
        assert "Москва" in fake.edits[-1].text and len(fake.sent) == len(messages)
        assert (
            "Shared working setlist" if language == "en" else "Общий рабочий сет-лист"
        ) in fake.edits[-1].text
        # The same existing message remains a valid target for a new song-list Reply.
        response = await processor.process(
            4,
            2,
            -100,
            "Комета\nКомета",
            private=False,
            reply_message_id=original_ids[-1],
            is_callback=False,
        )
        assert response and response.menu_card_id
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert len(snapshot["blocks"][0]["items"]) == 2


async def test_shared_operations_are_atomic_and_use_real_audit_actor(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await Groups(uow.store, world.admin).set_reminders(world.group.id, False)
    async with PostgresUnitOfWork(engine) as uow:
        await Administration(uow.store, "operator").execute(
            "group_reminders", world.group.id, {"enabled": "yes"}, "a" * 64
        )
    async with PostgresUnitOfWork(engine) as uow:
        rows = await uow.store.find(AdminAudit, action="group_reminders")
        assert {(r.actor, r.source) for r in rows} == {
            ("telegram:1", "telegram"),
            ("operator", "web"),
        }
    with pytest.raises(AppError):
        async with PostgresUnitOfWork(engine) as uow:
            await Groups(uow.store, world.stranger).set_reminders(world.group.id, False)
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(AdminAudit, action="group_reminders")) == 2


async def test_refresh_never_opens_private_cards_even_with_forged_queue_payload(engine, world):
    from setlist_bot.domain.entities import Context

    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(
            Context(
                user_id=world.member.id,
                chat_id=2,
                group_id=world.group.id,
                concert_id=world.concert.id,
            )
        )
        await Setlists(uow.store, world.member).edit_songs(
            world.group.id, world.concert.id, 0, ["Комета", "Комета"], private=True
        )
        before = (await uow.store.find(PersonalDraft, owner_id=world.member.id))[0].content
    processor, fake = Processor(engine), FakeTelegram()
    response = await processor.process(100, 2, 2, "/setlist", private=True)
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=fake) as bot:
        messages = await send_response(bot, 2, response)
        await processor.remember_response(2, response, messages)
        async with PostgresUnitOfWork(engine) as uow:
            await queue_card_refreshes(uow.store, world.group.id)
            assert not await uow.store.find(Outbox, support_kind="refresh")
            forged = Outbox(
                chat_id=2,
                text="must never leak",
                group_id=world.group.id,
                concert_id=world.concert.id,
                support_kind="refresh",
                payload={"card_id": str(response.menu_card_id)},
            )
            uow.store.add(forged)
        assert not await deliver_one_outbox(bot, engine)
        assert not fake.edits and len(fake.sent) == len(messages)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(PersonalDraft, owner_id=world.member.id))[0].content == before


async def test_production_mutations_require_fresh_matching_bot_signal(client, engine, world):
    from setlist_bot.admin.app import STATE

    await login(client)
    page = await client.get(f"/groups/{world.group.id}")
    path = f"/actions/group_reminders/{world.group.id}"
    proof = extract(await page.text(), path)
    client.app[STATE].settings.contour = "production"
    assert (await client.post(path, data=proof)).status == 409
    monitor = Monitor(identity_details(7430933141, "production_bot", "production", "server"))
    monitor.set("bot", "process_running")
    await monitor.flush(engine)
    assert (await client.post(path, data=proof, allow_redirects=False)).status == 303
