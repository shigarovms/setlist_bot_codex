import json
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message
from test_telegram import FakeTelegram

from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.support import Support
from setlist_bot.domain.entities import (
    Confirmation,
    Outbox,
    SupportMessage,
    SupportSettings,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.commands import DESCRIPTIONS, MENUS
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.telegram import Processor


def test_support_public_but_setup_hidden():
    assert "support" in DESCRIPTIONS
    assert "set_support_chat" not in DESCRIPTIONS
    assert all("set_support_chat" not in commands for commands in MENUS.values())
    for language in ("ru", "en"):
        assert "/support" in tr(language, "help")
        assert "set_support_chat" not in tr(language, "help")


async def test_owner_bootstrap_and_numeric_identity(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Support(uow.store, world.admin)
        with pytest.raises(AppError):
            await service.configure(-900, "other")
        with pytest.raises(AppError):
            await service.configure(1, "shigarovms")
        await service.configure(-900, "shigarovms")
    async with PostgresUnitOfWork(engine) as uow:
        # RU/EN: Переименование владельца не меняет права / renaming preserves ownership.
        await Support(uow.store, world.admin).configure(-901, "renamed")
        with pytest.raises(AppError):
            await Support(uow.store, world.stranger).configure(-902, "shigarovms")
        settings = (await uow.store.find(SupportSettings))[0]
        assert settings.chat_id == -901 and settings.owner_telegram_id == 1


async def test_support_round_trip_real_processor_and_outbox(engine, world):
    ids = count(1000)
    processor = Processor(engine, bot_username="unit_bot")

    async def send(user, chat, text, **kwargs):
        return await processor.process(
            next(ids), user, chat, text, private=chat > 0, is_callback=False, **kwargs
        )

    result = await send(1, -900, "/set_support_chat", sender_username="shigarovms")
    assert "настроен" in result.text
    prompt = await send(2, 2, "/support")
    assert prompt.force_reply and prompt.support_prompt_id
    message = Message(message_id=101, date=datetime.now(UTC), chat=Chat(id=2, type="private"))
    await processor.remember_response(2, prompt, [message])
    # Unrelated users and unrelated Reply targets must never be submitted.
    await send(3, 3, "Чужой ответ", reply_message_id=101)
    await send(2, 2, "Не то сообщение", reply_message_id=102)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Outbox)
    result = await send(2, 2, "Нужна помощь", reply_message_id=101)
    assert "передано" in result.text
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
        async with PostgresUnitOfWork(engine) as uow:
            route = (await uow.store.find(SupportMessage))[0]
            assert route.chat_id == -900 and route.message_id == 1
            assert "Нужна помощь" in session.sent[-1].text
            assert not await Support(uow.store, world.admin).reply(-901, 1, "Неверный чат")
        # Any real support-chat participant may answer, without group membership in the app.
        result = await send(3, -900, "Попробуйте ещё раз", reply_message_id=1)
        assert "очередь" in result.text
        await deliver_one_outbox(bot, engine)
        assert session.sent[-1].chat_id == 2
        assert "Попробуйте ещё раз" in session.sent[-1].text
        # New bot process can answer the old persisted support message.
        restarted = Processor(engine)
        await restarted.process(
            next(ids), 1, -900, "Ещё ответ", private=False, reply_message_id=1, is_callback=False
        )
        await deliver_one_outbox(bot, engine)
        assert session.sent[-1].chat_id == 2 and "Ещё ответ" in session.sent[-1].text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Confirmation, id=prompt.support_prompt_id)
        assert len(await uow.store.find(SupportMessage)) == 1


async def test_cancel_and_expired_support_prompt(engine, world):
    processor = Processor(engine)
    await processor.process(
        1, 1, -900, "/set_support_chat", private=False, sender_username="shigarovms"
    )
    prompt = await processor.process(2, 2, 2, "/support", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(Confirmation, prompt.support_prompt_id)
        row.command = json.dumps({"kind": "support", "prompt_id": 10})
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    result = await processor.process(
        3, 2, 2, "Просрочено", private=True, reply_message_id=10, is_callback=False
    )
    assert "истек" in result.text.lower() or "устарел" in result.text.lower()
    prompt = await processor.process(4, 2, 2, "/support", private=True)
    await processor.process(5, 2, 2, prompt.cancel_action, private=True, is_callback=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Confirmation, id=prompt.support_prompt_id)
        assert not await uow.store.find(Outbox)


async def test_action_notices_include_actor_and_approved_snapshot(engine, world):
    from datetime import date

    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        await Groups(uow.store, world.admin).create("Новый коллектив", -800)
        service = Setlists(uow.store, world.admin)
        concert = await service.create(world.group.id, date(2027, 1, 5), "Новый концерт")
        await service.edit_songs(world.group.id, concert.id, 0, ["Комета", "5 мин", "Комета"])
        await service.approve(world.group.id, concert.id, 1)
        events = await uow.store.find(Outbox)
        assert len(events) == 3
        assert all(e.chat_id == -900 and "Telegram ID: 1" in e.text for e in events)
        approved = next(e.text for e in events if "Утверждён" in e.text)
        assert approved.count("Комета") == 2 and "пауза  05:00" in approved
        with pytest.raises(AppError):
            await service.approve(world.group.id, concert.id, 1)
        assert len(await uow.store.find(Outbox)) == 3


async def test_group_support_link_and_no_configuration(engine, world):
    processor = Processor(engine, bot_username="unit_bot")
    result = await processor.process(1, 2, -100, "/support", private=False)
    assert "https://t.me/unit_bot?start=support" in result.text
    result = await processor.process(2, 2, 2, "/start support", private=True)
    assert "не настроен" in result.text


async def test_support_multi_chunk_reply_targets(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        assert await Support(uow.store, world.member).submit("Длинное обращение " * 350)
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        routes = await uow.store.find(SupportMessage)
        assert len(routes) > 1
        assert len(routes) == len(session.sent)
        assert all(route.message_id for route in routes)
        for route in routes:
            assert await Support(uow.store, world.admin).reply(-900, route.message_id, "Ответ")


async def test_reconfigure_redirects_pending_but_rejects_old_chat_replies(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        await Support(uow.store, world.member).submit("Помощь")
        await Support(uow.store, world.admin).configure(-901, "")
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
    assert session.sent[-1].chat_id == -901
    async with PostgresUnitOfWork(engine) as uow:
        assert not await Support(uow.store, world.admin).reply(-900, 1, "Нет доступа")
        assert await Support(uow.store, world.admin).reply(-901, 1, "Ответ")


async def test_blocked_user_does_not_block_queue_and_staff_are_notified(engine, world):
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        uow.store.add(Outbox(chat_id=2, text="Ответ", support_kind="reply"))
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=SendMessage(chat_id=2, text="Ответ"),
        message="Forbidden: bot was blocked by the user",
    )
    await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(Outbox, chat_id=2))[0].sent
        notice = (await uow.store.find(Outbox, chat_id=-900))[0]
        assert "не доставлен" in notice.text and not notice.sent
