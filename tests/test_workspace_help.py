from uuid import UUID, uuid4

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from test_collective_workflows import client as telegram_client
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_telegram import FakeTelegram
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.support import Support
from setlist_bot.domain.entities import Confirmation, Outbox, SupportMessage
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app


async def test_help_and_support_work_without_a_band_and_reuse_telegram_delivery(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
    send = telegram_client(engine)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        assert (await client.get("/miniapp/api/help")).status == 401
        help_ru = await get(client, 3, "help")
        assert help_ru["language"] == "ru"
        assert len(help_ru["topics"]) == 6
        catalog = next(topic for topic in help_ru["topics"] if topic["title"] == "Репертуар")
        assert "алиасам" in catalog["text"] and "/catalog" in catalog["commands"]
        await send("/language en", actor=3, private=True)
        help_en = await get(client, 3, "help")
        assert help_en["language"] == "en"
        assert help_en["topics"][0]["title"] == "Getting started"

        session = {"session": (await post(client, 3, "open_account", {}))["session"]}
        message = "Нужна помощь с «Кометой»\nСпасибо!"
        payload = {
            **session,
            "request_id": str(uuid4()),
            "text": f" {message} ",
            "user_id": str(world.member.id),
            "source": "browser",
            "chat_id": -100,
        }
        await post(client, 2, "support_submit", payload, 401)
        queued = await post(client, 3, "support_submit", payload)
        assert queued["status"] == "pending"
        assert await post(client, 3, "support_submit", payload) == queued
        assert await deliver_request(engine, queued["request"])
        receipt = await get(client, 3, "catalog_status", **session, request=queued["request"])
        assert receipt == {"request": queued["request"], "status": "saved"}
        assert await post(client, 3, "support_submit", payload) == receipt
        async with PostgresUnitOfWork(engine) as uow:
            row = await uow.store.get(Confirmation, UUID(queued["request"]))
            assert "Кометой" not in row.command
            (event,) = await uow.store.find(Outbox, support_kind="request")
            (route,) = await uow.store.find(SupportMessage, outbox_id=event.id)
            assert event.source == "miniapp" and event.actor == "telegram:3"
            assert route.user_id == world.stranger.id and route.chat_id == -900
            assert f"Telegram ID: 3)\n\n{message}" in event.text

    telegram = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=telegram) as bot:
        assert await deliver_one_outbox(bot, engine)
        assert telegram.sent[-1].chat_id == -900 and message in telegram.sent[-1].text
        async with PostgresUnitOfWork(engine) as uow:
            (route,) = await uow.store.find(SupportMessage)
            reply_to = route.message_id
        assert reply_to is not None
        await send("Ответ на обращение", actor=1, chat=-900, reply=reply_to)
        assert await deliver_one_outbox(bot, engine)
        assert telegram.sent[-1].chat_id == 3 and "Ответ на обращение" in telegram.sent[-1].text


async def test_support_validates_text_and_reports_unavailable_without_leaking_message(
    engine, world
):
    await setup_workspace(engine, world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = {"session": (await post(client, 2, "open_account", {}))["session"]}
        for text in (" ", "x" * 4001, "bad\x00", 1):
            await post(
                client,
                2,
                "support_submit",
                {**session, "request_id": str(uuid4()), "text": text},
                403,
            )
        band = await post(client, 2, "open_repertoire", {"band": str(world.group.id)})
        details = {"request_id": str(uuid4()), "text": "Отдельный текст"}
        await post(client, 2, "support_submit", {**band, **details}, 403)
        queued = await post(client, 2, "support_submit", {**session, **details})
        assert not await deliver_request(engine, queued["request"])
        receipt = await get(client, 2, "catalog_status", **session, request=queued["request"])
        assert receipt["status"] == "failed" and receipt["error"] == "support_unavailable"
        async with PostgresUnitOfWork(engine) as uow:
            row = await uow.store.get(Confirmation, UUID(queued["request"]))
            assert details["text"] not in row.command
            assert not await uow.store.find(Outbox, support_kind="request")
