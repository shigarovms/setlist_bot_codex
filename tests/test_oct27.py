import json
from datetime import UTC, date, datetime
from itertools import count

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message
from test_telegram import FakeTelegram

from setlist_bot.application import reminders
from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.display import entry_labels, short_duration
from setlist_bot.domain.entities import Confirmation, Group, Membership, Outbox, Reminder
from setlist_bot.domain.rules import AppError, pause_item
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.commands import menu_commands
from setlist_bot.transport.controller import Response
from setlist_bot.transport.render import render_setlist
from setlist_bot.transport.telegram import Processor, send_response


async def test_owner_multiple_admins_and_names(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Groups(uow.store, world.admin)
        group = await uow.store.get(Group, world.group.id)
        assert group.owner_id == world.admin.id
        changed = await service.register(group.id, "Илья")
        assert changed.role == "admin" and changed.status == "active"
        assert await service.name(group.id) == "Илья"
        member = (await uow.store.find(Membership, user_id=world.member.id, group_id=group.id))[0]
        await service.administrator(group.id, member.id, True)
        await Groups(uow.store, world.member).access(group.id, admin=True)
        with pytest.raises(AppError):
            await Groups(uow.store, world.member).administrator(group.id, changed.id, False)
        with pytest.raises(AppError):
            await service.administrator(group.id, changed.id, False)
        await service.administrator(group.id, member.id, False)
        assert member.status == "active" and member.role == "member"
        assert await service.name(world.other.id) != "Илья"


@pytest.mark.parametrize("songs,pause_label", [(9, " "), (10, "  ")])
def test_pause_numbering_and_compact_header(songs, pause_label):
    from uuid import uuid4

    from setlist_bot.domain.entities import Concert

    items = [
        {"title": f"Песня {i}", "duration_seconds": 60, "pause_before": None} for i in range(songs)
    ]
    items.insert(1, pause_item(123))
    assert entry_labels(items)[1] == pause_label
    assert entry_labels(items)[-1] == str(songs)
    concert = Concert(group_id=uuid4(), date=date(2027, 1, 1), performance_duration_seconds=6000)
    rendered = render_setlist(
        {"default_pause_seconds": 5, "blocks": [{"items": items}]}, concert, "ru", 5
    )
    assert "Пауза: 5сек · Выступаем: 100мин" in rendered
    assert f"{pause_label}  пауза" in rendered
    assert short_duration(123) == "2мин3сек"


async def test_approved_author_is_frozen_and_admin_can_approve(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Groups(uow.store, world.admin)
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        await service.administrator(world.group.id, member.id, True)
        await Groups(uow.store, world.member).register(world.group.id, "Илья")
        lists = Setlists(uow.store, world.member)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await lists.approve(world.group.id, world.concert.id, 1)
        await Groups(uow.store, world.member).register(world.group.id, "Новое имя")
        snapshot, version, approved = await lists.read(world.group.id, world.concert.id)
        assert approved and snapshot["approved_by"] == "Илья"
        assert "Одобрен: Илья · версия" in render_setlist(
            snapshot, world.concert, "ru", version, approved=True
        )


async def test_force_reply_one_message_no_cancel_carrier():
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        messages = await send_response(
            bot, 1, Response("Дата?", force_reply=True, cancel_action="input_cancel test")
        )
    assert len(messages) == len(session.sent) == 1
    assert session.sent[0].reply_markup.force_reply and "/cancel" in session.sent[0].text


async def test_proposal_becomes_same_working_panel(engine, world):
    processor = Processor(engine)
    ids = count(2000)

    async def send(text, chat=1, source=None):
        return await processor.process(
            next(ids),
            1,
            chat,
            text,
            private=chat > 0,
            is_callback=not text.startswith("/"),
            message_id=source,
        )

    await send(f"/use_group {world.group.id}")
    await send(f"/use_concert {world.concert.id}")
    await send("/suggestion Комета")
    await send("/publish")
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox))[0]
        action = event.buttons[0][1]
    result = await send(action, -100, 1)
    assert result.edit_message_ids == [1] and result.menu_card_id
    assert not any(action.startswith("apply") for _, action in result.buttons)
    again = await send(action, -100, 1)
    assert again.menu_card_id is None


async def test_reminders_once_and_opt_out_and_no_approved(engine, world):
    local = datetime(2027, 1, 1, 16, tzinfo=reminders.MOSCOW)
    async with PostgresUnitOfWork(engine) as uow:
        concert = await Setlists(uow.store, world.admin).create(world.group.id, date(2027, 1, 2))
        await reminders.queue(uow.store, concert.id, local)
        await reminders.queue(uow.store, concert.id, local)
        assert len(await uow.store.find(Reminder)) == 1
        group = await uow.store.get(Group, world.group.id)
        group.reminders_enabled = False
        await reminders.queue(uow.store, world.other_concert.id, local)
        # The other group's own reminder is independent of this group's preference.
        assert (await uow.store.find(Reminder, concert_id=concert.id))[0].day == local.date()
    processor = Processor(engine)
    denied = await processor.process(20, 2, -100, "/reminders on", private=False)
    assert "администратор" in denied.text.lower()
    accepted = await processor.process(21, 1, -100, "/reminders on", private=False)
    assert "включены" in accepted.text


def test_daily_offsets_and_command_order():
    from uuid import UUID

    at = datetime(2027, 1, 1, 12, tzinfo=UTC)
    due = reminders.due_at(UUID(int=3599), at)
    assert due.hour == 14 and due.minute == 59 and due.second == 59
    for menu in ("private", "group", "admin", "default"):
        assert [c.command for c in menu_commands(menu, "ru")][:6] == [
            "support",
            "groups",
            "concerts",
            "start",
            "add_new_songs",
            "help",
        ]


async def test_pause_minutes_undo_pause_and_position_buttons(engine, world):
    ids = count(3000)
    processor = Processor(engine)

    async def send(text, *, raw=False):
        return await processor.process(
            next(ids), 1, 1, text, private=True, is_callback=not text.startswith("/") and not raw
        )

    await send(f"/use_group {world.group.id}")
    await send(f"/use_concert {world.concert.id}")
    method = await send("/suggestion")
    start = next(action for _, action in method.buttons if action.startswith("pick_start "))
    panel = await send(start)
    label = next(label for row in panel.reply_keyboard for label in row if "Комета" in label)
    await send(label, raw=True)
    async with PostgresUnitOfWork(engine) as uow:
        row = next(
            r
            for r in await uow.store.find(Confirmation, user_id=world.admin.id)
            if json.loads(r.command).get("kind") == "song_selection"
        )
        data = json.loads(row.command)
        data["stage"] = "pause"
        row.command = json.dumps(data)
    result = await send("/picker_pause 3")
    assert "03:00" in result.text
    undone = await send("↩️ Удалить последнюю", raw=True)
    assert "03:00" not in undone.text and "Комета" in undone.text
    await send("/suggestion\nКомета\n3 мин\nКомета")
    choices = await send("/remove")
    pause_action = next(action for label, action in choices.buttons if "пауза" in label)
    confirmation = await send(pause_action)
    await send(next(action for _, action in confirmation.buttons if action.startswith("confirm ")))
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id, private=True
        )
        assert len(snapshot["blocks"][0]["items"]) == 2


async def test_cleanup_known_panels_confirmed_and_preserves_database(engine, world):
    from setlist_bot.domain.entities import PersonalDraft

    processor = Processor(engine)
    ids = count(5000)

    async def send(text, user=1):
        return await processor.process(
            next(ids), user, -100, text, private=False, is_callback=not text.startswith("/")
        )

    panel = await send(f"/use_concert {world.concert.id}")
    message = Message(message_id=80, date=datetime.now(UTC), chat=Chat(id=-100, type="group"))
    await processor.remember_response(-100, panel, [message])
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"], private=True)
    denied = await send("/cleanup_chat", user=2)
    assert not denied.buttons and "администратор" in denied.text.lower()
    confirm = await send("/cleanup_chat")
    assert "за последние 48 часов" in confirm.text
    assert confirm.buttons[0][0] == "Да, прибраться"
    result = await send(confirm.buttons[0][1])
    assert 80 in result.delete_message_ids
    async with PostgresUnitOfWork(engine) as uow:
        assert (await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id))[
            1
        ] == 1
        assert len(await uow.store.find(PersonalDraft)) == 1


async def test_navigation_replaced_but_current_setlist_not_automatically_deleted(engine, world):
    from setlist_bot.transport.telegram import delete_selection_message

    processor = Processor(engine)
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        first = await processor.process(6100, 1, -100, "/concerts", private=False)
        first_sent = await send_response(bot, -100, first)
        await processor.remember_response(-100, first, first_sent)
        second = await processor.process(
            6101, 1, -100, f"/use_concert {world.concert.id}", private=False
        )
        second_sent = await send_response(bot, -100, second)
        await processor.remember_response(-100, second, second_sent)
        assert first_sent[0].message_id in second.delete_message_ids
        assert not set(second.delete_message_ids) & {m.message_id for m in second_sent}
        for identifier in second.delete_message_ids:
            await delete_selection_message(bot, -100, identifier)
        assert session.deleted


async def test_concert_flags_and_exact_options_order(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await service.approve(world.group.id, world.concert.id, 1)
    processor = Processor(engine)
    concerts = await processor.process(6200, 1, -100, "/concerts", private=False)
    assert next(
        label for label, action in concerts.buttons if str(world.concert.id) in action
    ).startswith("✅ ")
    assert not next(
        label for label, action in concerts.buttons if str(world.concert2.id) in action
    ).startswith("✅ ")
    shown = await processor.process(
        6201, 1, -100, f"/use_concert {world.concert.id}", private=False
    )
    options = await processor.process(
        6202,
        1,
        -100,
        next(action for label, action in shown.buttons if label == "⚙️ Опции"),
        private=False,
        is_callback=True,
    )
    assert [label for label, _ in options.buttons] == [
        "Параметры экспорта",
        "⚙️ Параметры сет-листа",
        "Параметры выступления",
        "Прибраться в чате",
        "Связаться с поддержкой",
        "◀️ Назад",
    ]
    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.admin).reopen(world.group.id, world.concert.id, 2)
    concerts = await processor.process(6203, 1, -100, "/concerts", private=False)
    assert not next(
        label for label, action in concerts.buttons if str(world.concert.id) in action
    ).startswith("✅ ")


async def test_reminder_rechecks_approval_and_preferences_on_delivery(engine, world):
    at = datetime(2026, 12, 30, 16, tzinfo=reminders.MOSCOW)
    async with PostgresUnitOfWork(engine) as uow:
        await reminders.queue(uow.store, world.concert.id, at)
        assert len(await uow.store.find(Reminder)) == 1
        group = await uow.store.get(Group, world.group.id)
        group.reminders_enabled = False
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
    assert not session.sent
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(Outbox))[0].sent


async def test_explicit_song_numbers_skip_pause(engine, world):
    processor = Processor(engine)
    await processor.process(6300, 1, -100, f"/use_concert {world.concert.id}", private=False)
    await processor.process(6301, 1, -100, "/suggestion\nКомета\n3 мин\nКомета", private=False)
    confirm = await processor.process(6302, 1, -100, "/remove 2", private=False)
    await processor.process(6303, 1, -100, confirm.buttons[0][1], private=False, is_callback=True)
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        items = snapshot["blocks"][0]["items"]
        assert len(items) == 2 and items[1]["kind"] == "pause"


async def test_migration_backfills_owner_and_frozen_approval(database_url, engine, world):
    from test_migrations import alembic, clear_synthetic_audit

    from setlist_bot.domain.entities import Revision

    async with PostgresUnitOfWork(engine) as uow:
        await Groups(uow.store, world.admin).register(world.group.id, "Старый владелец")
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await service.approve(world.group.id, world.concert.id, 1)
        approved = (await uow.store.find(Revision, group_id=world.group.id, action="approved"))[0]
        approved.snapshot = {
            key: value for key, value in approved.snapshot.items() if key != "approved_by"
        }
    await clear_synthetic_audit(engine)
    alembic(database_url, "downgrade", "e42b8c190a76")
    alembic(database_url, "upgrade", "head")
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Group, world.group.id)).owner_id == world.admin.id
        snapshot, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert snapshot["approved_by"] == "Старый владелец"


async def test_admin_request_never_inherits_message_ids_from_another_chat(engine, world):
    from setlist_bot.application.setlist_menus import SetlistMenus

    processor = Processor(engine)
    await processor.process(6400, 2, -100, f"/use_concert {world.concert.id}", private=False)
    panel = await processor.process(6401, 2, -100, "/suggestion Комета", private=False)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(Confirmation, panel.menu_card_id)
        data = json.loads(row.command)
        data["message_ids"] = [777]
        row.command = json.dumps(data)
        request, admin, created = await SetlistMenus(uow.store, world.member, -100).request(
            row, data, "approve"
        )
        assert (
            created
            and admin.id == world.admin.id
            and json.loads(request.command)["message_ids"] == []
        )


async def test_group_nickname_is_used_in_admin_request(engine, world):
    processor = Processor(engine)
    async with PostgresUnitOfWork(engine) as uow:
        await Groups(uow.store, world.member).register(world.group.id, "Лёша — бас")
    await processor.process(6500, 2, -100, f"/use_concert {world.concert.id}", private=False)
    shown = await processor.process(6501, 2, -100, "/suggestion Комета", private=False)
    approve = next(action for label, action in shown.buttons if "Утвердить" in label)
    await processor.process(6502, 2, -100, approve, private=False, is_callback=True)
    async with PostgresUnitOfWork(engine) as uow:
        messages = await uow.store.find(Outbox, chat_id=1)
        assert messages and "Лёша — бас" in messages[0].text


async def test_stale_reminder_button_cannot_change_another_group(engine, world):
    processor = Processor(engine)
    await processor.process(6600, 1, 1, f"/use_group {world.group.id}", private=True)
    settings = await processor.process(6601, 1, 1, "/reminders", private=True)
    old_button = settings.buttons[0][1]
    await processor.process(6602, 1, 1, f"/use_group {world.other.id}", private=True)
    rejected = await processor.process(6603, 1, 1, old_button, private=True, is_callback=True)
    assert "устарел" in rejected.text.lower() or "истек" in rejected.text.lower()
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Group, world.other.id)).reminders_enabled
        assert (await uow.store.get(Group, world.group.id)).reminders_enabled
