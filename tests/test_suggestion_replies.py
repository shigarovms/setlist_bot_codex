from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from aiogram.types import Chat, Message
from menu_helpers import variants

from setlist_bot.application.setlists import Setlists
from setlist_bot.application.trash import TrashCleanup
from setlist_bot.domain.entities import Outbox, SetlistMessage, SongListTemplate, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


def messenger(engine):
    ids = count(1)

    async def send(text, *, actor=1, private=False, reply=None, remember=None):
        # Every update uses a new Processor: reply bindings must live in PostgreSQL.
        processor = Processor(engine)
        chat_id = actor if private else -100
        response = await processor.process(
            next(ids),
            actor,
            chat_id,
            text,
            private=private,
            reply_message_id=reply,
            display_name=f"Музыкант {actor}",
        )
        if remember is not None:
            message = Message(
                message_id=remember,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private" if private else "supergroup"),
            )
            await processor.remember_response(chat_id, response, [message])
        return response

    return send


def copy_button(response):
    return next(data for _, data in response.buttons if data.startswith("copy_list "))


async def test_empty_concert_button_and_reply_create_first_variant_for_another_member(
    engine, world
):
    send = messenger(engine)
    empty = await send(f"/use_concert {world.concert.id}", remember=100)
    prompt = await send(copy_button(empty), actor=2, remember=101)
    assert "Какой вариант вы предлагаете?" in prompt.text
    result = await send("Комета\nКомета", actor=2, reply=101)
    assert "Музыкант 2 · вариант 1" in dict((await variants(send, result, actor=2)).buttons)
    assert "1. Комета" in result.text and "2. Комета" in result.text
    async with PostgresUnitOfWork(engine) as uow:
        data, version, _ = await Setlists(uow.store, world.member).read(
            world.group.id, world.concert.id
        )
        assert version == 1 and len(data["blocks"][0]["items"]) == 2
        assert not await uow.store.find(Outbox)


@pytest.mark.parametrize("body", ["Комета\nКомета", "/suggestion\nКомета\nКомета"])
async def test_reply_to_working_message_uses_original_concert_after_switch(engine, world, body):
    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}", remember=100)
    await send(f"/use_concert {world.concert2.id}", actor=2)
    result = await send(body, actor=2, reply=100)
    assert "Новый год" in result.text and "вариант 1" in str(
        (await variants(send, result, actor=2)).buttons
    )
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        assert (await service.read(world.group.id, world.concert.id))[1] == 1
        assert (await service.read(world.group.id, world.concert2.id))[1] == 0


async def test_command_without_list_prompts_and_command_reply_keeps_anchor(engine, world):
    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}", remember=100)
    prompt = await send("/suggestion", remember=101)
    assert prompt.force_reply and "Какой вариант" in prompt.text
    await send(f"/use_concert {world.concert2.id}")
    prompt2 = await send("/suggestion", reply=100, remember=102)
    assert prompt2.force_reply
    result = await send("Комета", reply=102)
    assert "Новый год" in result.text
    assert "Не удалось" in (await send("/suggest")).text


async def test_stale_reply_and_stale_button_show_current_original_concert_without_overwrite(
    engine, world
):
    send = messenger(engine)
    old = await send(f"/use_concert {world.concert.id}", remember=100)
    await send("/add Комета")
    await send(f"/use_concert {world.concert2.id}", actor=2)
    stale = await send("Комета\nКомета", actor=2, reply=100)
    assert "уже изменились" in stale.text and "Новый год" in stale.text
    assert "1. Комета" in stale.text and "2. Комета" not in stale.text
    assert stale.suggestion_template_id and stale.monospace
    assert stale.menu_card_id is not None
    assert "уже изменились" in (await send(copy_button(old), actor=2)).text
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        assert (await service.read(world.group.id, world.concert.id))[1] == 1
        assert (await service.read(world.group.id, world.concert2.id))[1] == 0


async def test_private_reply_changes_only_owners_draft_and_does_not_publish(engine, world):
    send = messenger(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    shown = await send(f"/use_concert {world.concert.id}", actor=2, private=True, remember=100)
    prompt = await send(copy_button(shown), actor=2, private=True, remember=101)
    assert "/publish" in prompt.text
    own = await send("Комета\nКомета", actor=2, private=True, reply=101)
    assert "Личный черновик" in own.text
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        assert (await service.read(world.group.id, world.concert.id, private=True))[1] == 1
        assert (await service.read(world.group.id, world.concert.id))[1] == 0
        assert not await uow.store.find(Outbox)
    # Private message IDs have no meaning in another chat.
    assert await send("Комета", reply=101) is None


async def test_reply_checks_membership_approval_expiry_and_ignores_unrelated_messages(
    engine, world
):
    send = messenger(engine)
    shown = await send(f"/use_concert {world.concert.id}", remember=100)
    assert await send("Комета", reply=999) is None
    assert "Нет доступа" in (await send("Комета", actor=3, reply=100)).text
    await send("/suggestion\nКомета")
    approve = await send("/approve")
    approved = await send(approve.buttons[0][1], remember=102)
    assert not approved.suggestion_template_id
    assert await send("Комета", reply=102) is None
    rejected = await send("Комета", actor=2, reply=100)
    assert "уже изменились" in rejected.text or "утверждён" in rejected.text
    async with PostgresUnitOfWork(engine) as uow:
        template = await uow.store.get(SongListTemplate, shown.suggestion_template_id)
        template.expires_at = now() - timedelta(seconds=1)
    assert "Срок действия" in (await send("Комета", reply=100)).text
    async with PostgresUnitOfWork(engine) as uow:
        await TrashCleanup(uow.store).run()
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(SetlistMessage, chat_id=-100, message_id=100)


async def test_invalid_reply_is_atomic_and_can_be_corrected_on_same_prompt(engine, world):
    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}")
    await send("/suggestion", remember=100)
    invalid = await send("Комета\nXXXXXXXXZZZZ", reply=100)
    assert "Список не применён" in invalid.text and "Строка 2" in invalid.text
    result = await send("Комета", reply=100)
    assert "вариант 1" in str((await variants(send, result, actor=2)).buttons)


async def test_reply_anchor_does_not_revive_after_concert_deleted_and_restored(engine, world):
    from setlist_bot.application.trash import Trash

    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}", remember=100)
    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        concert = await trash.delete_concert(
            world.group.id,
            world.concert.id,
            await trash.concert_fingerprint(world.group.id, world.concert.id),
        )
        epoch = concert.lifecycle_version
    assert "удалён" in (await send("Комета", reply=100)).text
    async with PostgresUnitOfWork(engine) as uow:
        await Trash(uow.store, world.admin).restore_concert(world.group.id, world.concert.id, epoch)
    assert "Срок действия" in (await send("Комета", reply=100)).text
    fresh = await send(f"/use_concert {world.concert.id}", remember=101)
    assert copy_button(fresh)
    assert "1. Комета" in (await send("Комета", reply=101)).text
