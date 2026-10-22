from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TelegramUser

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.song_selection import SongSelection
from setlist_bot.domain.entities import (
    Confirmation,
    Outbox,
    PersonalDraft,
    Revision,
    Setlist,
    Song,
    Submission,
    now,
)
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor, make_router


def messenger(engine):
    updates = count(1)

    async def send(text, *, actor=2, private=True, raw=False, update_id=None, reply=None):
        return await Processor(engine).process(
            next(updates) if update_id is None else update_id,
            actor,
            actor if private else -100,
            text,
            private=private,
            is_callback=not text.startswith("/") and not raw,
            reply_message_id=reply,
        )

    return send


def button(response, prefix):
    return next(data for _, data in response.buttons if data.startswith(prefix + " "))


async def choose(send, world):
    await send(f"/use_group {world.group.id}")
    await send(f"/use_concert {world.concert.id}")
    method = await send("/suggestion")
    return await send(button(method, "pick_start"))


async def contents(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        return await service.read(world.group.id, world.concert.id, private=True)


async def test_confirm_replaces_only_private_draft_preserves_order_duplicates_and_is_idempotent(
    engine, world
):
    async with PostgresUnitOfWork(engine) as uow:
        second = await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Атомы", "duration_seconds": 248}
        )
        await Setlists(uow.store, world.member).edit_songs(
            world.group.id, world.concert.id, 0, ["Комета"], private=True
        )
    send = messenger(engine)
    picker = await choose(send, world)
    labels = {label.split(". ", 1)[1]: label for row in picker.reply_keyboard[1:] for label in row}
    for label in (labels["Комета"], labels["Атомы"], labels["Комета"]):
        selected = await send(label, raw=True)
        assert selected.delete_input and selected.reply_keyboard is None
    assert "1. Комета\n2. Атомы\n3. Комета" in selected.text
    assert (await contents(engine, world))[1] == 1
    review = await send("✅ Завершить", raw=True)
    assert review.remove_keyboard
    assert "Комета\nАтомы\nКомета" in review.text
    assert await send(labels["Атомы"], raw=True) is None
    confirm = button(review, "pick_confirm")
    saved = await send(confirm)
    assert "не опубликован" in saved.text
    snapshot, version, approved = await contents(engine, world)
    assert version == 2 and not approved
    assert [i["song_id"] for i in snapshot["blocks"][0]["items"]] == [
        str(world.song.id),
        str(second.id),
        str(world.song.id),
    ]
    assert "устарел" in (await send(confirm)).text
    assert (await contents(engine, world))[1] == 2
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Confirmation, user_id=world.member.id)
        assert not await uow.store.find(Outbox)
        assert not await uow.store.find(Submission)
        assert len(await uow.store.find(PersonalDraft)) == 1
        shared = (await uow.store.find(Setlist, concert_id=world.concert.id))[0]
        assert shared.version == 0 and not shared.approved
        assert len(await uow.store.find(Revision, setlist_id=shared.id)) == 1


@pytest.mark.parametrize("review_first", [False, True])
async def test_cancel_discards_selection_preserves_existing_draft_and_returns_input_menu(
    engine, world, review_first
):
    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.member).edit_songs(
            world.group.id, world.concert.id, 0, ["Комета", "Комета"], private=True
        )
    before = await contents(engine, world)
    send = messenger(engine)
    picker = await choose(send, world)
    await send(picker.reply_keyboard[1][0], raw=True)
    if review_first:
        review = await send("✅ Завершить", raw=True)
        result = await send(button(review, "pick_cancel"))
        assert button(result, "pick_start")
    else:
        result = await send("/cancel")
    assert result.remove_keyboard == (not review_first)
    assert await contents(engine, world) == before
    if not review_first:
        assert await send(picker.reply_keyboard[1][0], raw=True) is None
    async with PostgresUnitOfWork(engine) as uow:
        active = await SongSelection(uow.store, world.member, 2).active()
        if review_first:
            assert active and SongSelection.data(active)["selected"] == []
        else:
            assert active is None


@pytest.mark.parametrize("use_text_button", [False, True])
async def test_multiline_paste_without_reply_is_available_from_method_menu(
    engine, world, use_text_button
):
    send = messenger(engine)
    await send(f"/use_group {world.group.id}")
    await send(f"/use_concert {world.concert.id}")
    method = await send("/suggestion")
    assert button(method, "pick_start") and button(method, "pick_text")
    if use_text_button:
        prompt = await send(button(method, "pick_text"))
        assert prompt.force_reply
    invalid = await send("Комета\nXXXXXXXXZZZZ", raw=True)
    assert "Список не применён" in invalid.text
    assert (await contents(engine, world))[1] == 0
    saved = await send("Комета\nКомета", raw=True)
    assert "Личный черновик" in saved.text
    assert len((await contents(engine, world))[0]["blocks"][0]["items"]) == 2


async def test_popularity_uses_only_latest_approval_per_concert_and_freezes_roster(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        catalog = Catalog(uow.store, world.admin)
        alpha = await catalog.save(world.group.id, {"title": "Альфа", "duration_seconds": 60})
        gamma = await catalog.save(world.group.id, {"title": "Гамма", "duration_seconds": 60})
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"] * 5)
        await service.approve(world.group.id, world.concert.id, 1)
        await service.reopen(world.group.id, world.concert.id, 2)
        await service.edit_songs(world.group.id, world.concert.id, 3, ["Альфа"] * 2, replace=True)
        await service.approve(world.group.id, world.concert.id, 4)
        await service.edit_songs(world.group.id, world.concert2.id, 0, ["Комета"] * 2)
        await service.approve(world.group.id, world.concert2.id, 1)
        await service.edit_songs(world.other.id, world.other_concert.id, 0, ["Чужая песня"] * 10)
        await service.approve(world.other.id, world.other_concert.id, 1)
        archived = Song(group_id=world.group.id, title="Архив", duration_seconds=60, archived=True)
        uow.store.add(archived)
    send = messenger(engine)
    picker = await choose(send, world)
    labels = [s for row in picker.reply_keyboard[1:] for s in row]
    assert labels == ["1. Альфа", "2. Комета", "3. Гамма"]
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.reopen(world.group.id, world.concert2.id, 2)
        await service.edit_songs(world.group.id, world.concert2.id, 3, ["Гамма"] * 10, replace=True)
        await service.approve(world.group.id, world.concert2.id, 4)
        active = await SongSelection(uow.store, world.member, 2).active()
        assert [s["id"] for s in SongSelection.data(active)["songs"]] == [
            str(alpha.id),
            str(world.song.id),
            str(gamma.id),
        ]
    assert "1. Альфа" in (await send("1. Альфа", raw=True)).text


async def test_forty_per_page_two_columns_keep_page_and_selections(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        for i in range(44):
            uow.store.add(Song(group_id=world.group.id, title=f"Песня {i:02}", duration_seconds=60))
    send = messenger(engine)
    picker = await choose(send, world)
    assert picker.reply_keyboard[0] == ["✅ Завершить"]
    assert len(picker.reply_keyboard[1:-1]) == 20
    assert all(len(row) == 2 for row in picker.reply_keyboard[1:-1])
    first = picker.reply_keyboard[1][0]
    await send(first, raw=True)
    second_page = await send("Вперёд ▶️", raw=True)
    assert "2/2" in second_page.keyboard_text
    assert len([s for row in second_page.reply_keyboard[1:-1] for s in row]) == 5
    last = second_page.reply_keyboard[1][0]
    await send(last, raw=True)
    await send(last, raw=True)
    async with PostgresUnitOfWork(engine) as uow:
        active = await SongSelection(uow.store, world.member, 2).active()
        data = SongSelection.data(active)
        assert data["page"] == 1 and len(data["selected"]) == 3
    back = await send("◀️ Назад", raw=True)
    assert back.reply_keyboard == picker.reply_keyboard
    assert "Всего песен: 3" in back.text


async def test_stale_foreign_callbacks_context_switch_commands_and_update_dedup(engine, world):
    send = messenger(engine)
    picker = await choose(send, world)
    identifier = picker.selection_id.hex
    assert "устарел" in (await send(f"pick_confirm {identifier}", actor=1)).text
    assert "личном" in (await send(f"pick_confirm {identifier}", private=False)).text
    await send(picker.reply_keyboard[1][0], raw=True, update_id=900)
    assert await send(picker.reply_keyboard[1][0], raw=True, update_id=900) is None
    await send("✅ Завершить", raw=True)
    next_method = await send("/suggestion")
    assert "устарел" in (await send(f"pick_cancel {identifier}")).text
    next_picker = await send(button(next_method, "pick_start"))
    switched = await send(f"/use_concert {world.concert2.id}")
    assert switched.remove_keyboard
    assert await send(next_picker.reply_keyboard[1][0], raw=True) is None
    assert (await contents(engine, world))[1] == 0


@pytest.mark.parametrize("change", ["archive", "draft", "expire", "membership", "lifecycle"])
async def test_changed_state_cannot_overwrite_or_publish_draft(engine, world, change):
    send = messenger(engine)
    picker = await choose(send, world)
    await send(picker.reply_keyboard[1][0], raw=True)
    review = await send("✅ Завершить", raw=True)
    async with PostgresUnitOfWork(engine) as uow:
        if change == "archive":
            await Catalog(uow.store, world.admin).archive(world.group.id, world.song.id, 1, True)
        elif change == "draft":
            await Setlists(uow.store, world.member).edit_songs(
                world.group.id, world.concert.id, 0, ["Комета", "Комета"], private=True
            )
        elif change == "expire":
            session = await uow.store.get(Confirmation, picker.selection_id)
            session.expires_at = now() - timedelta(seconds=1)
        elif change == "membership":
            from setlist_bot.domain.entities import Membership

            member = (await uow.store.find(Membership, user_id=world.member.id))[0]
            member.status = "pending"
        else:
            from setlist_bot.domain.entities import Concert

            concert = await uow.store.get(Concert, world.concert.id)
            concert.lifecycle_version += 1
    result = await send(button(review, "pick_confirm"))
    assert "сохранён в личный" not in result.text
    async with PostgresUnitOfWork(engine) as uow:
        draft = (await uow.store.find(PersonalDraft, owner_id=world.member.id))[0]
        assert draft.version == (1 if change == "draft" else 0)
        assert not await uow.store.find(Outbox)


async def test_duplicate_titles_are_saved_by_song_id_and_limits_are_atomic(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        twin = Song(group_id=world.group.id, title="Комета", duration_seconds=60)
        uow.store.add(twin)
    send = messenger(engine)
    picker = await choose(send, world)
    assert picker.reply_keyboard[1] == ["1. Комета", "2. Комета"]
    await send("2. Комета", raw=True)
    async with PostgresUnitOfWork(engine) as uow:
        session = await uow.store.get(Confirmation, picker.selection_id)
        data = SongSelection.data(session)
        expected = data["selected"][0]
        data["selected"] = [expected] * 200
        SongSelection.write(session, data)
    assert "от 1 до 200" in (await send("2. Комета", raw=True)).text
    review = await send("✅ Завершить", raw=True)
    await send(button(review, "pick_confirm"))
    items = (await contents(engine, world))[0]["blocks"][0]["items"]
    assert len(items) == 200 and {i["song_id"] for i in items} == {expected}


class PickerTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.ids = count(100)
        self.sent = []
        self.edits = []
        self.deleted = []
        self.fail_delete = False
        self.fail_edit = False

    async def close(self):
        pass

    async def stream_content(self, url: str, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, DeleteMessage):
            self.deleted.append(method.message_id)
            if self.fail_delete:
                raise TelegramBadRequest(method, "message can't be deleted")
            return True
        if isinstance(method, EditMessageText):
            self.edits.append(method)
            if self.fail_edit:
                raise TelegramBadRequest(method, "message to edit not found")
        elif isinstance(method, SendMessage):
            self.sent.append(method)
        else:
            raise AssertionError(type(method))
        return Message(
            message_id=method.message_id if isinstance(method, EditMessageText) else next(self.ids),
            date=datetime.now(UTC),
            chat=Chat(id=int(method.chat_id), type="private"),
            from_user=TelegramUser(id=bot.id, is_bot=True, first_name="Bot"),
            text=method.text,
        )


@pytest.mark.parametrize("save", [True, False])
async def test_real_router_deletes_only_picker_input_edits_panel_and_survives_delete_failure(
    engine, world, save
):
    session = PickerTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "setlist_test_bot"))
        updates = count(1)
        actor = TelegramUser(id=2, is_bot=False, first_name="Музыкант")

        async def send(text, *, callback=False, reply=None):
            message = Message(
                message_id=next(session.ids),
                date=datetime.now(UTC),
                chat=Chat(id=2, type="private"),
                from_user=actor,
                text=text,
                reply_to_message=reply,
            )
            uid = next(updates)
            update = Update(
                update_id=uid,
                **(
                    {
                        "callback_query": CallbackQuery(
                            id=str(uid),
                            from_user=actor,
                            chat_instance="private",
                            message=message,
                            data=text,
                        )
                    }
                    if callback
                    else {"message": message}
                ),
            )
            await dispatcher.feed_update(bot, update)
            return message.message_id

        await send(f"/use_group {world.group.id}")
        await send(f"/use_concert {world.concert.id}")
        await send("/suggestion")
        start = session.sent[-1].reply_markup.inline_keyboard[1][0].callback_data
        await send(start, callback=True)
        keyboard = session.sent[-2].reply_markup
        assert keyboard.resize_keyboard and len(keyboard.keyboard[1]) == 1
        label = keyboard.keyboard[1][0].text
        baseline = len(session.sent)
        first_input = await send(label)
        assert first_input in session.deleted and len(session.sent) == baseline
        panel = session.edits[-1].message_id
        session.fail_delete = True
        await send(label)
        assert session.edits[-1].message_id == panel and "2. Комета" in session.edits[-1].text
        assert len(session.sent) == baseline
        unrelated = Message(
            message_id=99999,
            date=datetime.now(UTC),
            chat=Chat(id=2, type="private"),
            from_user=TelegramUser(id=bot.id, is_bot=True, first_name="Bot"),
            text="Unrelated",
        )
        ignored_input = await send(label, reply=unrelated)
        assert ignored_input not in session.deleted
        await send("✅ Завершить")
        assert session.sent[-1].reply_markup.remove_keyboard
        assert session.edits[-1].message_id == panel
        assert "Комета\nКомета" in session.edits[-1].text
        buttons = session.edits[-1].reply_markup.inline_keyboard
        assert len(buttons) == 1 and len(buttons[0]) == 2
        await send(buttons[0][0 if save else 1].callback_data, callback=True)
        assert (await contents(engine, world))[1] == (1 if save else 0)
        edits = len(session.edits)
        idle_input = await send("Случайный текст")
        assert idle_input not in session.deleted and len(session.edits) == edits


async def test_missing_panel_is_replaced_and_expanded_lists_keep_utf16_limits(engine, world):
    from setlist_bot.transport.controller import Response
    from setlist_bot.transport.telegram import send_response

    session = PickerTelegram()
    session.fail_edit = True
    text = "🎸 " + "Очень длинное название\n" * 400
    response = Response(text, edit_message_ids=[50], selection_id=UUID(int=1))
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        sent = await send_response(bot, 2, response)
    assert len(sent) > 1 and sent[0].message_id != 50
    assert "".join(m.text for m in session.sent) == text
    assert all(len(m.text.encode("utf-16-le")) // 2 <= 3500 for m in session.sent)
