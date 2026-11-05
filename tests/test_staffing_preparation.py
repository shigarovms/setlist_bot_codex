"""Temporary roster edits must never send invitations or overwrite concurrent changes."""

import json
from datetime import timedelta
from uuid import UUID

import pytest
from test_staffing_transport import button, messenger

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    Concert,
    Confirmation,
    GigInvitation,
    GigSlot,
    LineupTemplate,
    Outbox,
    StaffRole,
    User,
    now,
)
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.staffing_preparation import start_preparation


async def prepared(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        guitar = await service.role(world.group.id, "Гитара")
        vocals = await service.role(world.group.id, "Вокал")
        template = await service.template(
            world.group.id,
            "Основной",
            [
                {"role_id": str(guitar.id), "user_id": str(world.member.id)},
                {"role_id": str(vocals.id), "user_id": str(world.stranger.id)},
            ],
        )
        await service.contact(world.group.id, guitar.id, world.stranger.id)
        await service.apply_template(world.concert.id, template.id, template.version)
        await uow.store.flush()
        result = await start_preparation(
            Controller(uow.store, world.admin, 1, private=True), world.concert.id
        )
    return result


async def active_slots(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        return sorted(
            await uow.store.find(GigSlot, concert_id=world.concert.id, removed=False),
            key=lambda s: s.position,
        )


async def change_first(send, review):
    editor = await send(button(review, "Изменить состав выступления"), private=True, chat=1)
    card = await send(button(editor, "1. Гитара"), private=True, chat=1)
    changed = await send(
        next(callback for label, callback in card.buttons if label == "3"), private=True, chat=1
    )
    return changed


async def test_team_menu_has_only_sections_and_back_to_selected_group(engine, world):
    send = messenger(engine)
    selected = await send(f"/use_group {world.group.id}", private=True, chat=1)
    assert selected.buttons[0][0] == "Выступления Коллектив А"
    team = await send(button(selected, "Составы "), private=True, chat=1)
    assert [label for label, _ in team.buttons] == [
        "Участники коллектива",
        "Знакомые исполнители",
        "Составы",
        "◀️ Назад",
    ]
    back = await send(button(team, "Назад"), private=True, chat=1, callback=True)
    assert back.buttons == selected.buttons
    lineups = await send(button(team, "Составы"), private=True, chat=1)
    assert any("Создать состав" in label for label, _ in lineups.buttons)
    parent = await send(button(lineups, "Назад"), private=True, chat=1)
    assert [label for label, _ in parent.buttons] == [label for label, _ in team.buttons]
    contacts = await send(button(team, "Знакомые"), private=True, chat=1)
    assert any("Добавить исполнителя" in label for label, _ in contacts.buttons)


async def test_new_saved_lineup_name_and_members_are_separate_force_replies(engine, world):
    send = messenger(engine)
    team = await send(f"sf_team {world.group.id.hex}", private=True, chat=1)
    menu = await send(button(team, "Составы"), private=True, chat=1)
    name = await send(button(menu, "Создать состав"), private=True, chat=1, remember=801)
    assert name.force_reply and all(
        example in name.text for example in ("Основной", "Малый", "DJ set", "С танцорами")
    )
    members = await send("С танцорами", private=True, chat=1, reply=801, remember=802)
    assert members.force_reply and "С танцорами" in members.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(LineupTemplate, group_id=world.group.id)
    assert await send("Случайный текст", private=True, chat=1, reply=999) is None
    await send("Гитара: 2\nВокал", private=True, chat=1, reply=802)
    async with PostgresUnitOfWork(engine) as uow:
        rows = await uow.store.find(LineupTemplate, group_id=world.group.id)
        assert len(rows) == 1 and rows[0].name == "С танцорами" and len(rows[0].slots) == 2
    assert await send("Гитара: 2", private=True, chat=1, reply=802) is None


async def test_preview_back_discards_changes_and_keeps_real_roster_and_queue(engine, world):
    review = await prepared(engine, world)
    assert review.text.startswith(
        "Сейчас проверю занятость участников перед отправкой приглашений:"
    )
    assert "Гитара — 2" in review.text and "Вокал — 3" in review.text
    original = await active_slots(engine, world)
    send = messenger(engine)
    changed = await change_first(send, review)
    assert "Гитара — 3" in changed.text
    assert (await active_slots(engine, world))[0].preferred_user_id == world.member.id
    discarded = await send(button(changed, "Назад"), private=True, chat=1)
    assert discarded.text == review.text
    assert [slot.id for slot in await active_slots(engine, world)] == [slot.id for slot in original]
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)
        assert not await uow.store.find(Outbox, support_kind="staffing")


async def test_confirm_saves_once_without_invites_then_availability_send_invites(engine, world):
    review = await prepared(engine, world)
    send = messenger(engine)
    changed = await change_first(send, review)
    callback = button(changed, "Подтвердить состав")
    saved = await send(callback, private=True, chat=1)
    slots = await active_slots(engine, world)
    assert slots[0].preferred_user_id == world.stranger.id
    assert "Гитара — 3" in saved.text
    old = await send(callback, private=True, chat=1)
    assert "устарел" in old.text
    assert [slot.id for slot in await active_slots(engine, world)] == [slot.id for slot in slots]
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)
    available = await send(button(saved, "Проверить занятость"), private=True, chat=1)
    await send(button(available, "Пригласить основной состав"), private=True, chat=1)
    async with PostgresUnitOfWork(engine) as uow:
        invites = await uow.store.find(GigInvitation, concert_id=world.concert.id)
        assert len(invites) == 2 and all(row.user_id == world.stranger.id for row in invites)
        assert len(await uow.store.find(Outbox, support_kind="staffing", sent=False)) == 2


async def test_old_availability_send_is_invalid_after_returning_to_edit(engine, world):
    review = await prepared(engine, world)
    send = messenger(engine)
    availability = await send(button(review, "Проверить занятость"), private=True, chat=1)
    old_send = button(availability, "Пригласить основной состав")
    back = await send(button(availability, "Отмена"), private=True, chat=1)
    await send(button(back, "Изменить состав"), private=True, chat=1)
    denied = await send(old_send, private=True, chat=1)
    assert "устарел" in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)


async def test_add_and_remove_are_temporary_until_confirm_and_labels_explain_scope(engine, world):
    review = await prepared(engine, world)
    send = messenger(engine)
    editor = await send(button(review, "Изменить состав"), private=True, chat=1)
    await send(button(editor, "+ участник"), private=True, chat=1, remember=811)
    added = await send("Танцоры 2", private=True, chat=1, reply=811)
    assert "Танцоры" in added.text
    card = await send(button(added, "2. Вокал"), private=True, chat=1)
    assert any(label == "Удалить роль из выступления" for label, _ in card.buttons)
    removed = await send(button(card, "Удалить роль из выступления"), private=True, chat=1)
    assert "Вокал" not in removed.text and "Танцоры" in removed.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(StaffRole, name="Танцоры")
    await send(button(removed, "Подтвердить состав"), private=True, chat=1)
    async with PostgresUnitOfWork(engine) as uow:
        rows = await uow.store.find(StaffRole, name="Танцоры")
        assert len(rows) == 1
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)


async def test_back_to_choice_can_select_other_lineup_or_choose_later_without_duplicate_event(
    engine, world
):
    review = await prepared(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "DJ")
        await service.template(
            world.group.id, "DJ set", [{"role_id": str(role.id), "user_id": str(world.member.id)}]
        )
    send = messenger(engine)
    choices = await send(button(review, "Назад"), private=True, chat=1)
    assert any(label == "Выбрать состав позже" for label, _ in choices.buttons)
    new = await send(button(choices, "DJ set"), private=True, chat=1)
    assert "DJ — 2" in new.text and "Гитара" not in new.text
    choices = await send(button(new, "Назад"), private=True, chat=1)
    await send(button(choices, "Выбрать состав позже"), private=True, chat=1)
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Concert)) == 3
        event = await uow.store.get(Concert, world.concert.id)
        assert event.lineup_name == ""
        assert not await uow.store.find(GigSlot, concert_id=event.id, removed=False)
        assert not await uow.store.find(GigInvitation, concert_id=event.id)


async def test_preparation_checks_owner_expiry_and_concurrent_roster_changes(engine, world):
    review = await prepared(engine, world)
    send = messenger(engine)
    callback = button(review, "Изменить состав")
    assert "устарел" in (await send(callback, actor=2, private=True, chat=2)).text
    async with PostgresUnitOfWork(engine) as uow:
        envelope = await uow.store.get(Confirmation, UUID(callback.split()[1]))
        state = await uow.store.get(Confirmation, UUID(json.loads(envelope.command)["preview_id"]))
        state.expires_at = now() - timedelta(seconds=1)
    assert "устарел" in (await send(callback, private=True, chat=1)).text
    async with PostgresUnitOfWork(engine) as uow:
        state = await uow.store.get(Confirmation, state.id)
        state.expires_at = now() + timedelta(days=1)
    async with PostgresUnitOfWork(engine) as uow:
        event = await uow.store.get(Concert, world.concert.id)
        event.lineup_version += 1
    assert "устарел" in (await send(callback, private=True, chat=1)).text


async def test_unmodified_confirm_does_not_replace_slots(engine, world):
    review = await prepared(engine, world)
    original = await active_slots(engine, world)
    send = messenger(engine)
    editor = await send(button(review, "Изменить состав"), private=True, chat=1)
    await send(button(editor, "Подтвердить состав"), private=True, chat=1)
    assert [s.id for s in await active_slots(engine, world)] == [s.id for s in original]


async def test_unknown_candidate_can_be_retried_without_writing_temp_role(engine, world):
    review = await prepared(engine, world)
    send = messenger(engine)
    editor = await send(button(review, "Изменить состав"), private=True, chat=1)
    await send(button(editor, "+ участник"), private=True, chat=1, remember=821)
    unknown = await send("Танцоры @new_dancer", private=True, chat=1, reply=821)
    assert unknown.deliveries and "Ваш ввод сохранён" in unknown.text
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(User(telegram_id=777, username="new_dancer", username_seen_at=now()))
        assert not await uow.store.find(StaffRole, name="Танцоры")
    editor = await send(button(unknown, "Повторить"), private=True, chat=1)
    assert "Танцоры" in editor.text
    back = await send(button(editor, "Назад"), private=True, chat=1)
    assert back.text == review.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(StaffRole, name="Танцоры")
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)


async def test_preparation_service_rejects_foreign_role_and_revoked_admin(engine, world):
    from setlist_bot.domain.entities import Membership
    from setlist_bot.domain.rules import AppError

    review = await prepared(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        role = await Staffing(uow.store, world.admin).role(world.other.id, "Чужая роль")
        identifier = role.id
    with pytest.raises(AppError, match="forbidden"):
        async with PostgresUnitOfWork(engine) as uow:
            event = await uow.store.get(Concert, world.concert.id)
            await Staffing(uow.store, world.admin).replace_uninvited_lineup(
                event.id,
                event.lineup_version,
                "Чужой",
                [{"role_id": str(identifier), "user_id": None}],
            )
    original = await active_slots(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.admin.id)
        )[0]
        member.role = "member"
    denied = await messenger(engine)(button(review, "Изменить состав"), private=True, chat=1)
    assert "Изменить состав" not in [label for label, _ in denied.buttons]
    assert [slot.id for slot in await active_slots(engine, world)] == [slot.id for slot in original]


async def test_team_back_in_group_opens_same_band_menu(engine, world):
    send = messenger(engine)
    team = await send("/team", chat=-100)
    parent = await send(button(team, "Назад"), chat=-100)
    assert "Коллектив А" in parent.text
    assert parent.buttons[0] == ("Выступления Коллектив А", "concerts")
    assert parent.buttons[1][0] == "Репертуар Коллектив А"
    assert parent.buttons[2][0] == "Составы Коллектив А"
    assert "Коллектив Б" not in parent.text


async def test_empty_new_performance_can_choose_lineup_later(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        choices = await start_preparation(
            Controller(uow.store, world.admin, 1, private=True), world.concert.id
        )
    result = await messenger(engine)(button(choices, "Выбрать состав позже"), private=True, chat=1)
    assert result.buttons
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Concert)) == 3
        assert not await uow.store.find(GigSlot, concert_id=world.concert.id)
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)
