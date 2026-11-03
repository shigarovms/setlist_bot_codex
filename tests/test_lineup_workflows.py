"""Lineup lifecycle and performer consent; only synthetic users and the *_test database."""

import pytest
from aiogram import Bot
from test_staffing import invite, reply, setup_slots
from test_staffing_transport import button, make_staffing_due, messenger
from test_telegram import FakeTelegram

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    Concert,
    GigInvitation,
    GigSlot,
    LineupTemplate,
    Outbox,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.domain.staff_input import role_person
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox


@pytest.mark.parametrize("separator", [" ", "  ", "-", " - ", ":", ": ", "\t"])
def test_role_delimiters_preserve_hyphenated_names(separator):
    assert role_person("Бэк-вокал" + separator + "@singer", required=True) == (
        "Бэк-вокал",
        "@singer",
    )
    assert role_person("Мон. звукореж" + separator + "12345") == ("Мон. звукореж", "12345")
    assert role_person("Бэк-вокал") == ("Бэк-вокал", "")


async def template(engine, world, name="Малый", person=True):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "Бас-гитара")
        row = await service.template(
            world.group.id,
            name,
            [{"role_id": str(role.id), "user_id": str(world.member.id) if person else None}],
        )
        return row.id, row.version


async def test_lineup_edit_copy_delete_does_not_change_existing_performance(engine, world):
    identifier, version = await template(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        slots = await service.apply_template(world.concert.id, identifier, version)
        slot_id = slots[0].id
        await service.edit_template(identifier, version, name="Переименован", values=[])
        copy = await service.template(
            world.group.id, "Копия", [{"role_id": str(slots[0].role_id), "user_id": None}]
        )
        await service.edit_template(identifier, version + 1, delete=True)
        assert len(copy.slots) == 1
    async with PostgresUnitOfWork(engine) as uow:
        event = await uow.store.get(Concert, world.concert.id)
        assert event.lineup_name == "Малый"
        slot = await uow.store.get(GigSlot, slot_id)
        assert not slot.removed and slot.preferred_user_id == world.member.id


async def test_removed_role_cancels_unsent_invite_without_cancellation_dm(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(GigInvitation, identifier)
        queued = (await uow.store.find(Outbox, support_kind="staffing", sent=False))[0]
        assert queued.next_attempt_at <= now()
        await Staffing(uow.store, world.admin).remove_slot(slot, 1)
        assert row.status == "cancelled" and (await uow.store.get(GigSlot, slot)).removed
        assert not await uow.store.find(Outbox, support_kind="staffing", sent=False)
    await make_staffing_due(engine)
    session = FakeTelegram()
    async with Bot("123456:TEST", session=session) as bot:
        assert not await deliver_one_outbox(bot, engine)
    assert not session.sent
    with pytest.raises(AppError, match="input_expired"):
        await reply(engine, world.member, identifier)


async def test_invite_is_due_immediately_and_cancel_after_delivery_notifies(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    session = FakeTelegram()
    async with Bot("123456:TEST", session=session) as bot:
        assert await deliver_one_outbox(bot, engine)
        async with PostgresUnitOfWork(engine) as uow:
            row = await uow.store.get(GigInvitation, identifier)
            assert row.notified_at
            await Staffing(uow.store, world.admin).remove_slot(slot, 1)
        assert await deliver_one_outbox(bot, engine)
    assert len(session.sent) == 2 and "Участие отменено" in session.sent[-1].text


async def test_repeated_reminders_do_not_duplicate_messages(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        for _ in range(3):
            await service.remind(identifier, 1)
        rows = await uow.store.find(Outbox, support_kind="staffing", sent=False)
        assert len(rows) == 1 and rows[0].next_attempt_at <= now()
    await make_staffing_due(engine)
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).remind(identifier, 1)
        assert not await uow.store.find(Outbox, support_kind="staffing", sent=False)


async def test_roles_can_be_removed_and_readded_without_reusing_historical_positions(engine, world):
    slot, _ = await setup_slots(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = (await uow.store.get(GigSlot, slot)).role_id
        await service.remove_slot(slot, 1)
        added = await service.add_slot(world.concert.id, role)
        assert added.position == 2 and not added.removed
        with pytest.raises(AppError, match="input_expired"):
            await service.remove_slot(slot, 1)


async def test_non_admin_and_other_band_cannot_change_saved_lineup(engine, world):
    identifier, version = await template(engine, world)
    for person in (world.member, world.stranger):
        with pytest.raises(AppError):
            async with PostgresUnitOfWork(engine) as uow:
                await Staffing(uow.store, person).edit_template(identifier, version, delete=True)
    with pytest.raises(AppError, match="input_expired"):
        async with PostgresUnitOfWork(engine) as uow:
            await Staffing(uow.store, world.admin).apply_template(
                world.other_concert.id, identifier, version
            )


async def test_unknown_person_preserves_input_and_resolves_before_writing_any_roles(engine, world):
    send = messenger(engine)
    await send(f"/use_group {world.group.id}", private=True, chat=1)
    team = await send("/team", private=True, chat=1)
    await send(button(team, "Создать состав"), private=True, chat=1, remember=710)
    unknown = await send(
        "Большой\nВокал 2\nБэк-вокал: @new_guitarist", private=True, chat=1, reply=710
    )
    assert "Ваш ввод сохранён" in unknown.text
    assert len(unknown.deliveries) == 1 and "Коллектив А" in unknown.deliveries[0][1].text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(StaffRole, group_id=world.group.id)
        assert not await uow.store.find(LineupTemplate, group_id=world.group.id)
        user = User(telegram_id=500, username="new_guitarist", username_seen_at=now())
        uow.store.add(user)
    saved = await send(button(unknown, "Повторить"), private=True, chat=1)
    assert "Команда и составы" in saved.text
    async with PostgresUnitOfWork(engine) as uow:
        assert len((await uow.store.find(LineupTemplate, group_id=world.group.id))[0].slots) == 2
    stale = await send(button(unknown, "Повторить"), private=True, chat=1)
    assert "Команда и составы" not in stale.text


async def test_creation_one_lineup_auto_applied_but_not_invited(engine, world):
    identifier, _ = await template(engine, world)
    send = messenger(engine)
    preview = await send("/concert 23.04 Клуб Космос. Москва. 45 мин", chat=-100)
    assert "Состав: Малый" in preview.text
    created = await send(
        next(a for _, a in preview.buttons if a.startswith("confirm ")), chat=-100, callback=True
    )
    assert created.deliveries and created.deliveries[0][1].text == (
        "Сейчас проверю занятость участников перед отправкой приглашений."
    )
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Concert, title="Клуб Космос"))[0]
        assert event.lineup_name == "Малый"
        slots = await uow.store.find(GigSlot, concert_id=event.id, removed=False)
        assert len(slots) == 1 and slots[0].preferred_user_id == world.member.id
        assert not await uow.store.find(GigInvitation, concert_id=event.id)
    # Explicit choice may appear in any separated field.
    await template(engine, world, "Большой")
    preview = await send("/concert 25.04 Малый состав. Москва. 30 мин. Новый клуб", chat=-100)
    assert "Состав: Малый" in preview.text and "Новый клуб" in preview.text


async def test_creation_multiple_lineups_requires_owner_bound_choice_and_stale_revision_rejected(
    engine, world
):
    identifier, version = await template(engine, world)
    await template(engine, world, "Большой")
    send = messenger(engine)
    choice = await send("/concert 25.04 Клуб. Москва. 30 мин", chat=-100)
    assert "Выберите состав" in choice.text
    callback = button(choice, "Малый")
    denied = await send(callback, actor=2, chat=-100)
    assert "устарел" in denied.text
    preview = await send(callback, chat=-100)
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).edit_template(
            identifier, version, name="Малый новый"
        )
    stale = await send(
        next(a for _, a in preview.buttons if a.startswith("confirm ")), chat=-100, callback=True
    )
    assert "устарел" in stale.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Concert, title="Клуб")


async def test_main_lineup_onboarding_and_groups_entry(engine, world):
    send = messenger(engine)
    preview = await send("/new_band Новая группа", chat=-300)
    created = await send(
        next(a for _, a in preview.buttons if a.startswith("group_create_yes ")),
        chat=-300,
        callback=True,
    )
    assert "Настройте основной состав" in created.text
    assert created.deliveries and created.deliveries[0][1].buttons
    await send(button(created, "Настроить основной"), chat=-300, remember=720)
    await send("Вокал: 2\nГитара", chat=-300, reply=720)
    async with PostgresUnitOfWork(engine) as uow:
        row = (await uow.store.find(LineupTemplate, name="Основной"))[0]
        group_id = row.group_id
    selected = await send(f"/use_group {group_id}", private=True, chat=1)
    assert selected.buttons[0][1] == f"group_concerts {group_id.hex}"
    assert selected.buttons[1][0] == "Составы Новая группа"


async def test_admin_replace_confirmed_person_keeps_source_until_approval(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    await reply(engine, world.member, identifier)
    send = messenger(engine)
    screen = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    assert any(label.startswith("✅ 1. Гитара: поменять") for label, _ in screen.buttons)
    card = await send(button(screen, "1. Гитара"), private=True, chat=1)
    candidates = await send(button(card, "Пригласить другого"), private=True, chat=1)
    assert "Выбрать исполнителя" in candidates.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, identifier)).status == "confirmed"


async def test_busy_command_rename_visible_and_old_alias_hidden(engine, world):
    from setlist_bot.transport.commands import DESCRIPTIONS

    assert "set_no_concert_days" in DESCRIPTIONS and "busy" not in DESCRIPTIONS
    send = messenger(engine)
    result = await send("/set_no_concert_days 31.12.2026", private=True, chat=1)
    assert "31.12" in result.text


async def test_saved_lineup_ui_copy_edit_remove_role_and_delete(engine, world):
    identifier, version = await template(engine, world)
    send = messenger(engine)
    menu = await send(f"sf_team {world.group.id.hex}", private=True, chat=1)
    card = await send(button(menu, "Малый"), private=True, chat=1)
    copy_prompt = await send(button(card, "Копировать"), private=True, chat=1, remember=740)
    assert copy_prompt.force_reply
    copied = await send("Большой", private=True, chat=1, reply=740)
    role = await send(button(copied, "1. Бас-гитара"), private=True, chat=1)
    empty = await send(button(role, "Удалить роль"), private=True, chat=1)
    assert "1." not in empty.text
    confirm = await send(button(empty, "Удалить состав"), private=True, chat=1)
    assert "сохранятся" in confirm.text
    await send(button(confirm, "Подтвердить"), private=True, chat=1)
    async with PostgresUnitOfWork(engine) as uow:
        rows = await uow.store.find(LineupTemplate, group_id=world.group.id)
        assert len(rows) == 1 and rows[0].id == identifier and rows[0].version == version


async def test_empty_lineup_setup_during_creation_preserves_performance_fields(engine, world):
    send = messenger(engine)
    preview = await send("/concert 24.04 Клуб. Москва. 45 мин", chat=-100)
    setup = await send(button(preview, "Настроить основной"), chat=-100, remember=750)
    assert setup.force_reply
    preview = await send("Бас-гитара: 2\nВокал", chat=-100, reply=750)
    assert "Клуб" in preview.text and "45мин" in preview.text and "Состав: Основной" in preview.text
    created = await send(
        next(a for _, a in preview.buttons if a.startswith("confirm ")), chat=-100, callback=True
    )
    assert created is not None
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Concert, title="Клуб"))[0]
        assert event.city == "Москва" and event.lineup_name == "Основной"
        assert len(await uow.store.find(GigSlot, concert_id=event.id, removed=False)) == 2
        assert not await uow.store.find(GigInvitation, concert_id=event.id)


async def test_reminder_callback_does_not_accept_other_user_or_chat(engine, world):
    slot, _ = await setup_slots(engine, world)
    await invite(engine, world.admin, slot, world.member.id)
    send = messenger(engine)
    lineup = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    card = await send(button(lineup, "1. Гитара"), private=True, chat=1)
    callback = button(card, "напоминание")
    denied = await send(callback, actor=2, private=True, chat=1)
    assert "устарел" in denied.text
    denied = await send(callback, private=True, chat=999)
    assert "устарел" in denied.text


async def test_staffing_migration_preserves_delivered_invites_and_refuses_role_loss(
    engine, world, database_url
):
    from test_migrations import alembic

    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    await make_staffing_due(engine)
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine)
    alembic(database_url, "downgrade", "b63e2a71d940")
    alembic(database_url, "upgrade", "head")
    alembic(database_url, "check")
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(GigInvitation, identifier)
        assert row.status == "pending" and row.notified_at
        await Staffing(uow.store, world.admin).remove_slot(slot, 1)
    result = alembic(database_url, "downgrade", "b63e2a71d940", check=False)
    assert result.returncode != 0 and "would lose lineup data" in result.stderr


async def test_stale_role_removal_rejected_after_participant_confirms(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    send = messenger(engine)
    screen = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    card = await send(button(screen, "1. Гитара"), private=True, chat=1)
    confirmation = await send(button(card, "Удалить роль"), private=True, chat=1)
    await reply(engine, world.member, identifier)
    rejected = await send(button(confirmation, "Подтвердить"), private=True, chat=1)
    assert "устарел" in rejected.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not (await uow.store.get(GigSlot, slot)).removed
        assert (await uow.store.get(GigInvitation, identifier)).status == "confirmed"


async def test_changed_date_rejects_previously_selected_candidate(engine, world):
    from setlist_bot.application.concert_details import ConcertDetails

    slot, _ = await setup_slots(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        await ConcertDetails(uow.store, world.admin).update(
            world.group.id, world.concert.id, "concert_date", "2027-02-03"
        )
    with pytest.raises(AppError, match="input_expired"):
        async with PostgresUnitOfWork(engine) as uow:
            await Staffing(uow.store, world.admin).invite(slot, world.member.id, 1)


async def test_large_saved_lineups_and_creation_choices_are_paginated(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        role = await Staffing(uow.store, world.admin).role(world.group.id, "Г" * 100)
        for index in range(17):
            await Staffing(uow.store, world.admin).template(
                world.group.id,
                f"Состав {index:02}",
                [{"role_id": str(role.id), "user_id": None} for _ in range(40)],
            )
    send = messenger(engine)
    choices = await send("/concert 24.04 Клуб. Москва. 45мин", chat=-100)
    assert len(choices.buttons) == 17  # 15 lineups, next, without lineup
    second = await send(button(choices, "▶"), chat=-100)
    assert any(label == "Состав 16" for label, _ in second.buttons)
    menu = await send(f"sf_team {world.group.id.hex}", private=True, chat=1)
    card = await send(button(menu, "Состав 00"), private=True, chat=1)
    assert len(card.text) < 4096
    assert len(card.text.splitlines()) == 41 and card.text.splitlines()[-1].startswith("40.")
    assert sum(label[:1].isdigit() for label, _ in card.buttons) == 8
    current = card
    for page in range(1, 5):
        current = await send(button(current, "▶"), private=True, chat=1)
        assert current.text == card.text
        role_labels = [label for label, _ in current.buttons if label[:1].isdigit()]
        assert [int(label.partition(".")[0]) for label in role_labels] == list(
            range(page * 8 + 1, page * 8 + 9)
        )
    previous = await send(button(current, "◀"), private=True, chat=1)
    assert previous.text == card.text
    assert next(label for label, _ in previous.buttons if label[:1].isdigit()).startswith("25.")
