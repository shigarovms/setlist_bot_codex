import json
from datetime import UTC, datetime
from itertools import count

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TelegramUser
from menu_helpers import options, variants
from test_telegram import FakeTelegram

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlist_menus import CARD, REQUEST, SetlistMenus
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Confirmation, Membership, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.telegram import Processor, make_router


def sender(engine):
    ids = count(1)

    async def send(
        command, *, actor=1, private=False, chat=-100, remember=None, source=None, reply=None
    ):
        processor = Processor(engine)
        response = await processor.process(
            next(ids),
            actor,
            actor if private else chat,
            command,
            private=private,
            message_id=source,
            is_callback=not command.startswith("/") and reply is None,
            reply_message_id=reply,
            display_name=f"Музыкант {actor}",
        )
        if remember is not None:
            await processor.remember_response(
                actor if private else chat,
                response,
                [
                    Message(
                        message_id=remember,
                        date=datetime.now(UTC),
                        chat=Chat(
                            id=actor if private else chat,
                            type="private" if private else "supergroup",
                        ),
                    )
                ],
            )
        return response

    return send


def action(response, label):
    return next(command for title, command in response.buttons if title == label)


async def prepared(send, world, *, actor=1):
    await send(f"/use_concert {world.concert.id}", actor=actor)
    return await send("/suggestion\nКомета", actor=actor, remember=50)


async def approved(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        return (await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id))[2]


async def requests(engine):
    async with PostgresUnitOfWork(engine) as uow:
        return [
            row
            for row in await uow.store.find(Confirmation)
            if SetlistMenus.data(row).get("kind") == REQUEST
        ]


async def test_three_levels_variants_refresh_and_no_context_changes_during_navigation(
    engine, world
):
    send = sender(engine)
    empty = await send(f"/use_concert {world.concert.id}", remember=40)
    assert [label for label, _ in empty.buttons] == [
        "Предложить свой вариант",
        "⚙️ Опции",
        "✅ Утвердить",
    ]
    assert "📋 Варианты" not in dict((await options(send, empty)).buttons)
    assert (await send(action(empty, "✅ Утвердить"), actor=2, source=40)).callback_notice
    assert not await requests(engine)
    first = await send("/suggestion\nКомета", remember=50)
    assert (await send(action(first, "⚙️ Опции"), source=51)).callback_notice
    assert (await send(action(first, "⚙️ Опции"), chat=-200)).callback_notice
    await send(f"/use_concert {world.concert.id}", actor=2)
    await send("/suggestion\nКомета\nКомета", actor=2)
    await send(f"/use_concert {world.concert2.id}")
    choices = await variants(send, first)
    assert choices.markup_only and choices.edit_message_ids == [50] and choices.text == ""
    assert {"Музыкант 1 · вариант 1", "Музыкант 2 · вариант 1"} <= dict(choices.buttons).keys()
    original = await send(action(choices, "Музыкант 1 · вариант 1"))
    assert "Новый год" in original.text and original.text.count("Комета") == 1
    assert all(len(callback.encode()) <= 64 for _, callback in choices.buttons)
    back = await send(action(choices, "◀️ Назад"))
    main = await send(action(back, "◀️ Назад"))
    assert main.markup_only and main.text == ""


async def test_admin_approves_immediately_reopen_requires_one_time_confirmation(engine, world):
    send = sender(engine)
    card = await prepared(send, world)
    result = await send(action(card, "✅ Утвердить"), source=50, remember=50)
    assert await approved(engine, world)
    assert result.edit_message_ids == [50] and "Утверждённый" in result.text
    edit = action(result, "✏️ Редактировать")
    question = await send(edit, source=50)
    assert question.markup_only and "Статус „Утверждён“ будет снят" in question.text
    yes = action(question, "✅ Да, вернуть")
    cancelled = await send(action(question, "❌ Отмена"), source=50)
    assert cancelled.markup_only and await approved(engine, world)
    assert (await send(yes, source=50)).callback_notice
    assert await approved(engine, world)
    question = await send(edit, source=50)
    reopened = await send(action(question, "✅ Да, вернуть"), source=50, remember=50)
    assert not await approved(engine, world)
    assert "✅ Утвердить" in dict(reopened.buttons)
    assert (await send(action(card, "✅ Утвердить"), source=50)).callback_notice
    reply = await send("Комета\nКомета", actor=2, reply=50)
    assert "2. Комета" in reply.text


@pytest.mark.parametrize("accept", [True, False])
async def test_member_approval_request_is_private_to_admin_deduplicated_and_idempotent(
    engine, world, accept
):
    send = sender(engine)
    card = await prepared(send, world, actor=2)
    button = action(card, "✅ Утвердить")
    pending = await send(button, actor=2, source=50)
    assert pending.callback_notice and not await approved(engine, world)
    assert "уже отправили" in (await send(button, actor=2, source=50)).callback_notice
    rows = await requests(engine)
    assert len(rows) == 1 and rows[0].user_id == world.admin.id
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox, chat_id=1))[0]
        assert "Музыкант 2" in event.text and "Коллектив А" in event.text and "Комета" in event.text
        decision = event.buttons[0 if accept else 1][1]
    assert (await send(decision, actor=2, private=True)).callback_notice
    result = await send(decision, private=True)
    assert await approved(engine, world) == accept
    if accept:
        assert result.deliveries[0][0] == -100
        assert "✏️ Редактировать" in dict(result.deliveries[0][1].buttons)
    assert (await send(decision, private=True)).callback_notice
    assert await approved(engine, world) == accept


async def test_member_reopen_request_keeps_approved_until_admin_accepts(engine, world):
    send = sender(engine)
    card = await prepared(send, world)
    confirmed = await send(action(card, "✅ Утвердить"), source=50, remember=50)
    proposal = action(confirmed, "✏️ Редактировать")
    await send(proposal, actor=2, source=50)
    assert await approved(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        events = await uow.store.find(Outbox, chat_id=-100)
        assert any(
            "Музыкант 2" in e.text and "редактирование утверждённого" in e.text for e in events
        )
        request = (await uow.store.find(Outbox, chat_id=1))[0]
    await send(request.buttons[0][1], private=True)
    assert not await approved(engine, world)


@pytest.mark.parametrize("change", ["version", "catalog", "membership"])
async def test_request_revalidates_exact_version_catalog_and_permissions(engine, world, change):
    send = sender(engine)
    card = await prepared(send, world, actor=2)
    await send(action(card, "✅ Утвердить"), actor=2, source=50)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox, chat_id=1))[0]
        if change == "version":
            await Setlists(uow.store, world.member).edit_songs(
                world.group.id, world.concert.id, 1, ["Комета"]
            )
        elif change == "catalog":
            await Catalog(uow.store, world.admin).save(
                world.group.id, {"title": "Изменено", "duration_seconds": 100}, world.song.id, 1
            )
        else:
            member = (
                await uow.store.find(Membership, user_id=world.admin.id, group_id=world.group.id)
            )[0]
            member.role = "member"
    assert (await send(event.buttons[0][1], private=True)).callback_notice
    assert not await approved(engine, world)


async def test_private_draft_menus_cannot_approve_or_expose_other_owners_data(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    card = await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    assert [label for label, _ in card.buttons] == [
        "Предложить этот вариант",
        "Изменить",
        "⚙️ Опции",
    ]
    assert (await send(action(card, "⚙️ Опции"), private=True)).callback_notice
    forged = f"slact {card.menu_card_id.hex} approve"
    assert (await send(forged, actor=2, private=True)).callback_notice
    assert not await requests(engine) and not await approved(engine, world)


async def test_navigation_uses_only_edit_reply_markup_and_export_keeps_setlist_text(engine, world):
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "test_bot"))
        ids = count(1)
        user = TelegramUser(id=1, is_bot=False, first_name="Админ")

        async def feed(text, source=None):
            uid = next(ids)
            message = source or Message(
                message_id=1000 + uid,
                date=datetime.now(UTC),
                chat=Chat(id=-100, type="supergroup"),
                from_user=user,
                text=text,
            )
            update = Update(
                update_id=uid,
                **(
                    {
                        "callback_query": CallbackQuery(
                            id=str(uid),
                            from_user=user,
                            chat_instance="chat",
                            message=message,
                            data=text,
                        )
                    }
                    if source
                    else {"message": message}
                ),
            )
            await dispatcher.feed_update(bot, update)

        await feed(f"/use_concert {world.concert.id}")
        await feed("/suggestion\nКомета")
        card = session.sent[-1]
        await feed(
            next(
                row[0].callback_data
                for row in card.reply_markup.inline_keyboard
                if row[0].text == "✅ Утвердить"
            ),
            Message(
                message_id=len(session.sent),
                date=datetime.now(UTC),
                chat=Chat(id=-100, type="supergroup"),
                from_user=TelegramUser(id=bot.id, is_bot=True, first_name="Bot"),
                text=card.text,
            ),
        )
        root = session.edits[-1]
        source = Message(
            message_id=len(session.sent),
            date=datetime.now(UTC),
            chat=Chat(id=-100, type="supergroup"),
            from_user=TelegramUser(id=bot.id, is_bot=True, first_name="Bot"),
            text=root.text,
        )
        before = len(session.sent)
        await feed(
            next(
                r[0].callback_data
                for r in root.reply_markup.inline_keyboard
                if r[0].text == "⚙️ Опции"
            ),
            source,
        )
        opts = session.markups[-1].reply_markup
        await feed(
            next(r[0].callback_data for r in opts.inline_keyboard if r[0].text == "📋 Варианты"),
            source,
        )
        await feed(session.markups[-1].reply_markup.inline_keyboard[-1][0].callback_data, source)
        await feed(
            next(
                r[0].callback_data
                for r in root.reply_markup.inline_keyboard
                if r[0].text == "Экспорт"
            ),
            source,
        )
        assert len(session.sent) == before and len(session.edits) == 1
        assert source.text == root.text and len(session.markups) == 4


@pytest.mark.parametrize("blocked", [False, True])
async def test_admin_request_outbox_delivers_or_reports_unavailable_dm(engine, world, blocked):
    class Delivery(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            if blocked and isinstance(method, SendMessage) and method.chat_id == 1:
                raise TelegramForbiddenError(method, "bot was blocked by the user")
            return await super().make_request(bot, method, timeout)

    send = sender(engine)
    card = await prepared(send, world, actor=2)
    await send(action(card, "✅ Утвердить"), actor=2, source=50)
    session = Delivery()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        for _ in range(4):
            await deliver_one_outbox(bot, engine)
    data = SetlistMenus.data((await requests(engine))[0])
    assert data["status"] == ("undeliverable" if blocked else "pending")
    if blocked:
        assert any("Не удалось доставить" in event.text for event in session.sent)
        again = await send(action(card, "✅ Утвердить"), actor=2, source=50)
        assert "уже отправили" not in again.callback_notice
    else:
        assert any(
            event.chat_id == 1 and event.reply_markup.inline_keyboard for event in session.sent
        )


async def test_cancel_does_not_remove_cards_or_administrator_requests(engine, world):
    send = sender(engine)
    card = await prepared(send, world, actor=2)
    await send(action(card, "✅ Утвердить"), actor=2, source=50)
    await send("/cancel", private=True)
    await send("/cancel", actor=2)
    assert (await options(send, card, actor=2)).markup_only
    assert len(await requests(engine)) == 1
    async with PostgresUnitOfWork(engine) as uow:
        assert any(
            json.loads(r.command).get("kind") == CARD for r in await uow.store.find(Confirmation)
        )


async def test_cached_export_survives_card_approval_without_replacing_new_menu(engine, world):
    from menu_helpers import export_command

    send = sender(engine)
    card = await prepared(send, world)
    card = await send(action(card, "✅ Утвердить"), source=50, remember=50)
    menu = await send(await export_command(send, card))
    command = next(data for _, data in menu.buttons if data.startswith("export_file "))
    original = await send(command)
    repeated = await send(command)
    assert repeated.files == original.files
    assert repeated.markup_only


async def test_permanent_concert_purge_removes_menu_snapshots_and_requests(engine, world):
    from datetime import timedelta

    from setlist_bot.application.trash import Trash, TrashCleanup
    from setlist_bot.domain.entities import Concert, now

    send = sender(engine)
    card = await prepared(send, world, actor=2)
    await send(action(card, "✅ Утвердить"), actor=2, source=50)
    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        await trash.delete_concert(
            world.group.id,
            world.concert.id,
            await trash.concert_fingerprint(world.group.id, world.concert.id),
        )
        concert = await uow.store.get(Concert, world.concert.id)
        concert.deleted_at = now() - timedelta(days=31)
    async with PostgresUnitOfWork(engine) as uow:
        await TrashCleanup(uow.store).run()
    async with PostgresUnitOfWork(engine) as uow:
        assert all(
            SetlistMenus.data(row).get("concert_id") != str(world.concert.id)
            for row in await uow.store.find(Confirmation)
        )


async def test_private_admin_decision_updates_original_group_card_through_router(engine, world):
    from setlist_bot.domain.entities import SetlistMessage

    send = sender(engine)
    card = await prepared(send, world, actor=2)
    await send(action(card, "✅ Утвердить"), actor=2, source=50)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox, chat_id=1))[0]
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "test_bot"))
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=999,
                callback_query=CallbackQuery(
                    id="999",
                    chat_instance="private",
                    from_user=TelegramUser(id=1, is_bot=False, first_name="Админ"),
                    message=Message(
                        message_id=900,
                        date=datetime.now(UTC),
                        chat=Chat(id=1, type="private"),
                        from_user=TelegramUser(id=bot.id, is_bot=True, first_name="Bot"),
                        text=event.text,
                    ),
                    data=event.buttons[0][1],
                ),
            ),
        )
    assert await approved(engine, world)
    edit = session.edits[-1]
    assert edit.chat_id == -100 and edit.message_id == 50
    assert "✏️ Редактировать" in str(edit.reply_markup)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(SetlistMessage, chat_id=-100, message_id=50)
