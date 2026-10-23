import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from aiogram.types import Chat, Message

from setlist_bot.domain.entities import ChatBinding, Confirmation, Group, Membership, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.commands import menu_commands
from setlist_bot.transport.telegram import Processor


def messenger(engine):
    updates = count(1)

    async def send(
        text,
        *,
        chat=-300,
        actor=1,
        admin=True,
        private=False,
        reply=None,
        remember=None,
        callback=False,
    ):
        processor = Processor(engine)
        response = await processor.process(
            next(updates),
            actor,
            chat,
            text,
            private=private,
            chat_admin=admin,
            reply_message_id=reply,
            is_callback=callback,
        )
        if remember is not None and response is not None:
            await processor.remember_response(
                chat,
                response,
                [
                    Message(
                        message_id=remember,
                        date=datetime.now(UTC),
                        chat=Chat(id=chat, type="private" if private else "supergroup"),
                    )
                ],
            )
        return response

    return send


def button(response, prefix):
    return next(data for _, data in response.buttons if data.startswith(prefix + " "))


async def bound_group(engine, chat=-300):
    async with PostgresUnitOfWork(engine) as uow:
        bindings = await uow.store.find(ChatBinding, chat_id=chat)
        return await uow.store.get(Group, bindings[0].group_id) if bindings else None


async def test_reply_dialog_creates_using_existing_membership_and_ignores_unrelated_messages(
    engine, world
):
    send = messenger(engine)
    prompt = await send("/group", remember=100)
    assert prompt.force_reply and prompt.group_creation_id
    assert "Создание нового коллектива" in prompt.text
    assert await bound_group(engine) is None
    assert await send("Просто обсуждение") is None
    assert await send("Не название", reply=999) is None
    assert await send("Чужой ответ", actor=2, reply=100) is None
    assert await send("Ответ в другом чате", chat=-400, reply=100) is None
    await send("/help", reply=100)
    title = "Рок-группа «Атмосфера» <&>"
    preview = await send(title, reply=100)
    assert title in preview.text and len(preview.buttons) == 3
    assert await bound_group(engine) is None
    assert await send("Второй ответ", reply=100) is None
    created = await send(button(preview, "group_create_yes"), callback=True)
    assert title in created.text
    group = await bound_group(engine)
    assert group.name == title
    async with PostgresUnitOfWork(engine) as uow:
        membership = (await uow.store.find(Membership, group_id=group.id))[0]
        assert membership.user_id == world.admin.id and membership.role == "admin"
        assert membership.status == "active"
        assert not await uow.store.find(Confirmation, chat_id=-300)
    assert "устарел" in (await send(button(preview, "group_create_yes"), callback=True)).text


@pytest.mark.parametrize("command", ["/group", "/group Название", "/group " + "x" * 201])
async def test_bound_chat_rejected_before_name_prompt_or_validation(engine, world, command):
    send = messenger(engine)
    response = await send(command, chat=-100)
    assert response.text == "В этом чате уже создан коллектив."
    assert not response.force_reply and response.group_creation_id is None
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Confirmation, chat_id=-100)
    assert (await bound_group(engine, -100)).id == world.group.id


async def test_edit_name_rotates_prompt_and_confirmation_and_invalid_input_can_be_retried(
    engine, world
):
    send = messenger(engine)
    await send("/group", remember=100)
    invalid = await send("x" * 201, reply=100, remember=101)
    assert invalid.force_reply and "от 1 до 200" in invalid.text
    assert await send("Старый ответ", reply=100) is None
    preview = await send("Первое название", reply=101)
    changed = await send(button(preview, "group_create_edit"), callback=True, remember=102)
    assert changed.force_reply
    assert "устарел" in (await send(button(preview, "group_create_yes"), callback=True)).text
    assert await send("Повтор старого ответа", reply=101) is None
    blank = await send("   ", reply=102, remember=103)
    assert blank.force_reply
    final = await send("  Новое название  ", reply=103)
    await send(button(final, "group_create_yes"), callback=True)
    assert (await bound_group(engine)).name == "Новое название"


@pytest.mark.parametrize("via_command", [False, True])
async def test_cancel_clears_only_own_dialog_and_old_buttons_cannot_create(
    engine, world, via_command
):
    send = messenger(engine)
    await send("/group", remember=100)
    await send("/group", actor=2, remember=200)
    preview = await send("Название", reply=100)
    if via_command:
        await send("/cancel")
    else:
        response = await send(button(preview, "group_create_cancel"), callback=True)
        assert "отменено" in response.text
    assert "устарел" in (await send(button(preview, "group_create_yes"), callback=True)).text
    assert await bound_group(engine) is None
    other = await send("Коллектив второго администратора", actor=2, reply=200)
    assert button(other, "group_create_yes")


async def test_fast_command_private_chat_and_admin_checks_remain_compatible(engine, world):
    send = messenger(engine)
    assert "администратор" in (await send("/group", admin=False)).text
    assert "общем чате" in (await send("/group", private=True, chat=1)).text
    await send("/group", remember=100)
    preview = await send("Название", reply=100)
    foreign = await send(button(preview, "group_create_yes"), actor=2, callback=True)
    assert "устарел" in foreign.text
    demoted = await send(button(preview, "group_create_yes"), admin=False, callback=True)
    assert "администратор" in demoted.text and await bound_group(engine) is None
    created = await send("/group Быстрый коллектив")
    assert "Быстрый коллектив" in created.text
    assert (await bound_group(engine)).name == "Быстрый коллектив"
    assert "устарел" in (await send(button(preview, "group_create_yes"), callback=True)).text


async def test_expired_dialog_and_restarted_prompt_do_not_accept_old_answers(engine, world):
    send = messenger(engine)
    prompt = await send("/group", remember=100)
    async with PostgresUnitOfWork(engine) as uow:
        session = await uow.store.get(Confirmation, prompt.group_creation_id)
        session.expires_at = now() - timedelta(seconds=1)
    assert await send("Поздний ответ", reply=100) is None
    await send("/group", remember=101)
    assert await send("Старый ответ", reply=100) is None
    preview = await send("Название", reply=101)
    async with PostgresUnitOfWork(engine) as uow:
        session = (await uow.store.find(Confirmation, chat_id=-300))[0]
        session.expires_at = now() - timedelta(seconds=1)
    assert "устарел" in (await send(button(preview, "group_create_yes"), callback=True)).text
    assert await bound_group(engine) is None


async def test_concurrent_admins_can_create_only_one_group_in_same_chat(engine, world):
    send = messenger(engine)
    await send("/group", remember=100)
    await send("/group", actor=2, remember=200)
    first = await send("Первый", reply=100)
    second = await send("Второй", actor=2, reply=200)
    results = await asyncio.gather(
        send(button(first, "group_create_yes"), callback=True),
        send(button(second, "group_create_yes"), actor=2, callback=True),
    )
    assert sum("уже создан" in result.text for result in results) == 1
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(ChatBinding, chat_id=-300)) == 1
        group = await bound_group(engine)
        assert len(await uow.store.find(Membership, group_id=group.id)) == 1
    assert (await send("/group")).text == "В этом чате уже создан коллектив."


def test_group_menu_has_requested_description_and_remains_admin_scoped():
    commands = {command.command: command.description for command in menu_commands("admin", "ru")}
    assert commands["group"] == "Создать новый коллектив"
    assert "group" not in [command.command for command in menu_commands("private", "ru")]
