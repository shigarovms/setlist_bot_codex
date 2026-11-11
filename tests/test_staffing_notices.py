"""Notification context, self-assignment and invitation-scoped current setlist access."""

import json

import pytest
from aiogram import Bot
from test_staffing import invite, reply, setup_slots
from test_staffing_transport import button, messenger
from test_telegram import FakeTelegram

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    Confirmation,
    GigInvitation,
    Group,
    Membership,
    Outbox,
    PersonalDraft,
    User,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.staffing import invitation_view
from setlist_bot.transport.telegram import send_response


async def test_admin_self_assignment_is_confirmed_and_idempotent_without_invitation(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.admin.id)
    assert await invite(engine, world.admin, slot, world.admin.id) == identifier
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(GigInvitation, identifier)
        assert row.status == "confirmed"
        assert not await uow.store.find(Outbox, support_kind="staffing", sent=False)
        card = await invitation_view(
            Controller(uow.store, world.admin, 1, private=True), identifier
        )
        assert any(label == "Открыть сет-лист" for label, _ in card.buttons)
        assert not any(
            "PDF" in label or "печати" in label or "Подтверждаю" in label
            for label, _ in card.buttons
        )


async def test_self_assignment_still_checks_availability_and_needs_explicit_override(engine, world):
    one, two = await setup_slots(engine, world)
    await invite(engine, world.admin, one, world.admin.id)
    with pytest.raises(AppError, match="staff_busy"):
        await invite(engine, world.admin, two, world.admin.id)
    identifier = await invite(engine, world.admin, two, world.admin.id, force=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, identifier)).status == "confirmed"


async def test_bulk_default_assignment_confirms_only_the_acting_admin(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "Вокал")
        template = await service.template(
            world.group.id,
            "Основной",
            [
                {"role_id": str(role.id), "user_id": str(world.admin.id)},
                {"role_id": str(role.id), "user_id": str(world.member.id)},
            ],
        )
        slots = await service.apply_template(world.concert.id, template.id, template.version)
        await uow.store.flush()
        await service.invite_defaults(world.concert.id, {str(s.id): s.version for s in slots})
        rows = await uow.store.find(GigInvitation, concert_id=world.concert.id)
        assert {row.user_id: row.status for row in rows} == {
            world.admin.id: "confirmed",
            world.member.id: "pending",
        }
        queued = await uow.store.find(Outbox, support_kind="staffing", sent=False)
        assert len(queued) == 1 and queued[0].chat_id == 2


async def test_admin_reply_notice_contains_only_lineup_action(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    await reply(engine, world.member, identifier)
    session = FakeTelegram()
    async with Bot("123456:TEST", session=session) as bot:
        # First stale invitation is discarded because the recipient has already answered.
        assert not await deliver_one_outbox(bot, engine)
        assert await deliver_one_outbox(bot, engine)
    assert "Получен ответ на приглашение" in session.sent[-1].text
    buttons = [b for row in session.sent[-1].reply_markup.inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["Открыть состав выступления"]


async def test_substitution_notice_does_not_mix_in_own_performer_buttons(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.admin.id)
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).request_replacement(identifier)
    session = FakeTelegram()
    async with Bot("123456:TEST", session=session) as bot:
        assert await deliver_one_outbox(bot, engine)
    assert "Запрошена подмена" in session.sent[-1].text
    buttons = [b.text for row in session.sent[-1].reply_markup.inline_keyboard for b in row]
    assert buttons == ["Подобрать подмену", "Открыть состав выступления"]


async def test_candidate_acceptance_notice_keeps_substitution_approval(engine, world):
    slot, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, slot, world.member.id)
    await reply(engine, world.member, source)
    async with PostgresUnitOfWork(engine) as uow:
        request = await Staffing(uow.store, world.member).request_replacement(source)
        request_id = request.id
    candidate = await invite(
        engine, world.admin, slot, world.stranger.id, replacement_id=request_id
    )
    await reply(engine, world.stranger, candidate)
    async with PostgresUnitOfWork(engine) as uow:
        response = await invitation_view(
            Controller(uow.store, world.admin, 1, private=True), candidate, admin_notice=True
        )
        assert [label for label, _ in response.buttons] == [
            "✅ Утвердить подмену",
            "Открыть состав выступления",
        ]


@pytest.mark.parametrize("style", ["mono", "article"])
async def test_own_participation_opens_current_shared_list_without_private_draft(
    engine, world, style
):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.stranger.id)
    await reply(engine, world.stranger, identifier)
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(Group, world.group.id)).setlist_style = style
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Секретный черновик", "duration_seconds": 123}
        )
        await service.edit_songs(
            world.group.id, world.concert.id, 0, ["Секретный черновик"], private=True
        )
    send = messenger(engine)
    card = await send(f"sf_view {identifier.hex}", actor=3, private=True, chat=3)
    callback = button(card, "Открыть сет-лист")
    view = await send(callback, actor=3, private=True, chat=3)
    assert "Комета" in view.text and "Секретный черновик" not in view.text
    assert not any(action.startswith("sf_pdf") for _, action in view.buttons)
    assert bool(view.articles) == (style == "article")
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 1, ["Комета", "Комета"])
    view = await send(callback, actor=3, private=True, chat=3)
    assert view.text.count("Комета") == 3
    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.admin).approve(world.group.id, world.concert.id, 2)
    view = await send(callback, actor=3, private=True, chat=3)
    assert any(label == "Экспорт" for label, _ in view.buttons)
    export = await send(button(view, "Экспорт"), actor=3, private=True, chat=3)
    assert any("музыкантов" in label for label, _ in export.buttons)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(PersonalDraft, owner_id=world.stranger.id)
        service = Setlists(uow.store, world.admin)
        _, version, _ = await service.read(world.group.id, world.concert.id)
        await service.reopen(world.group.id, world.concert.id, version)
    view = await send(callback, actor=3, private=True, chat=3)
    assert not any(action.startswith("sf_pdf") for _, action in view.buttons)
    stale_export = await send(f"sf_pdf_m {identifier.hex}", actor=3, private=True, chat=3)
    assert not stale_export.files
    denied = await send(callback, actor=2, private=True, chat=2)
    assert "Комета" not in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).remove_slot(slot, 1)
    denied = await send(callback, actor=3, private=True, chat=3)
    assert "Комета" not in denied.text


async def test_full_lineup_text_uses_group_names_and_buttons_alone_are_paginated(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        for index in range(10):
            role = await service.role(world.group.id, f"Роль {index + 1}")
            slot = await service.add_slot(world.concert.id, role.id)
            if index == 0:
                identifier = await service.invite(slot.id, world.member.id, slot.version)
                invitation_id = identifier.id
            if index == 9:
                await service.invite(slot.id, world.stranger.id, slot.version)
        membership = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        membership.musical_role = "Илья — наш басист"
        person = await uow.store.get(User, world.member.id)
        person.display_name = "Другое имя"
        person.username = "private_handle"
    await reply(engine, world.member, invitation_id)
    send = messenger(engine)
    first = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    assert first.text.splitlines()[4] == "✅ 1. Роль 1 — Илья — наш басист"
    assert first.text.splitlines()[-1] == "10. Роль 10"
    assert (
        len(first.text.splitlines()) == 14
        and "Другое имя" not in first.text
        and "@" not in first.text
    )
    assert all("ждём" not in line and "пригласить" not in line for line in first.text.splitlines())
    second = await send(button(first, "▶"), private=True, chat=1)
    assert second.text == first.text and second.buttons[0][0].startswith("9.")
    assert not any(
        label.startswith("Изменить") or label == "Выбрать состав" for label, _ in first.buttons
    )
    callback = button(first, "Составы ")
    async with PostgresUnitOfWork(engine) as uow:
        from uuid import UUID

        session = await uow.store.get(
            Confirmation,
            UUID(callback.split()[1]),
        )
        assert json.loads(session.command)["event_id"] == str(world.concert.id)


async def test_role_spelling_is_kept_when_reusing_a_previously_lowercase_role(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        lower = await service.role(world.group.id, "звукорежиссер")
        upper = await service.role(world.group.id, "Звукорежиссер")
        assert upper.id == lower.id
        await service.add_slot(world.concert.id, upper.id)
    send = messenger(engine)
    screen = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    assert screen.text == world.group.name + "\n\n31.12 · Новый год\n\n1. Звукорежиссер"
    assert screen.buttons[0][0].startswith("1. Звукорежиссер:")


async def test_very_long_full_lineup_uses_telegram_splitting_without_losing_roles(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "З" * 100)
        for _ in range(40):
            await service.add_slot(world.concert.id, role.id)
    screen = await messenger(engine)(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    session = FakeTelegram()
    async with Bot("123456:TEST", session=session) as bot:
        await send_response(bot, 1, screen)
    assert len(session.sent) >= 2
    assert all(len(row.text) <= 4096 for row in session.sent)
    assert "40. " + "З" * 100 in "".join(row.text for row in session.sent)
