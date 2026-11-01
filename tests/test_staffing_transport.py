from datetime import timedelta

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from test_group_creation import messenger as base_messenger
from test_staffing import invite, reply, setup_slots
from test_telegram import FakeTelegram

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    BusyPeriod,
    GigInvitation,
    LineupTemplate,
    StaffReplacement,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.telegram import Processor


def button(response, text):
    return next(action for label, action in response.buttons if text in label)


def messenger(engine):
    original = base_messenger(engine)

    async def send(value, **kwargs):
        kwargs.setdefault("callback", value.startswith("sf_"))
        return await original(value, **kwargs)

    return send


async def test_force_reply_template_contact_and_invitation_flow_without_group_membership(
    engine, world
):
    send = messenger(engine)
    await send(f"/use_group {world.group.id}", private=True, chat=1)
    await send(f"/use_concert {world.concert.id}", private=True, chat=1)
    team = await send("/team", private=True, chat=1)
    prompt = await send(button(team, "шаблон"), private=True, chat=1, remember=100)
    assert prompt.force_reply
    assert await send("Постороннее сообщение", actor=2, private=True, chat=1, reply=100) is None
    await send("Малый\nВокал  3\nDJ", private=True, chat=1, reply=100)
    async with PostgresUnitOfWork(engine) as uow:
        template = (await uow.store.find(LineupTemplate, group_id=world.group.id))[0]
        assert len(template.slots) == 2
        await Staffing(uow.store, world.admin).apply_template(
            world.concert.id, template.id, template.version
        )
    lineup = await send("/lineup", private=True, chat=1)
    assert "не назначен" in lineup.text
    candidates = await send(button(lineup, "1. Вокал"), private=True, chat=1)
    await send(
        next(action for label, action in candidates.buttons if "пересечений" in label),
        private=True,
        chat=1,
    )
    async with PostgresUnitOfWork(engine) as uow:
        row = (await uow.store.find(GigInvitation, user_id=world.stranger.id))[0]
        identifier = row.id
    session = FakeTelegram()
    async with Bot("123456:TEST", session=session) as bot:
        assert await deliver_one_outbox(bot, engine)
    assert "Приглашение на выступление" in session.sent[-1].text
    c = Processor(engine)
    view = await c.process(2001, 3, 3, f"sf_view {identifier.hex}", private=True)
    accepted = await c.process(2002, 3, 3, button(view, "Подтверждаю"), private=True)
    assert "подтверждено" in accepted.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, identifier)).status == "confirmed"


async def test_blocked_invitation_reports_to_admin_and_retry_is_available(engine, world):
    one, _ = await setup_slots(engine, world)
    await invite(engine, world.admin, one, world.stranger.id)

    class Blocked(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, SendMessage) and method.chat_id == 3:
                raise TelegramForbiddenError(method=method, message="blocked")
            return await super().make_request(bot, method, timeout)

    session = Blocked()
    async with Bot("123456:TEST", session=session) as bot:
        assert not await deliver_one_outbox(bot, engine)
        assert await deliver_one_outbox(bot, engine)
    assert "Не удалось доставить" in session.sent[-1].text
    assert any(
        "Повторить" in b.text for row in session.sent[-1].reply_markup.inline_keyboard for b in row
    )


async def test_needed_cancellation_requires_confirmation_and_revokes_guest_access(engine, world):
    one, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, one, world.stranger.id)
    await reply(engine, world.stranger, identifier)
    send = messenger(engine)
    await send(f"/use_group {world.group.id}", private=True, chat=1)
    lineup = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    confirm = await send(button(lineup, "Инструмент не нужен"), private=True, chat=1)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, identifier)).status == "confirmed"
    await send(button(confirm, "Подтвердить"), private=True, chat=1)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, identifier)).status == "cancelled"
    response = await send(f"sf_guest {identifier.hex}", actor=3, private=True, chat=3)
    assert "Нет доступа" in response.text


async def test_busy_inline_input_matches_reply_and_other_user_cannot_remove_it(engine, world):
    send = messenger(engine)
    response = await send("/busy 31.12.2026", private=True, chat=1)
    assert "31.12" in response.text
    remove = button(response, "Снять отметку")
    denied = await send(remove, actor=2, private=True, chat=2)
    assert "устарел" in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(BusyPeriod, user_id=world.admin.id)) == 1


async def test_admin_can_perform_and_record_own_past_participation_once(engine, world):
    from setlist_bot.domain.concerts import today
    from setlist_bot.domain.entities import Concert, StaffContact

    one, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, one, world.admin.id)
    await reply(engine, world.admin, identifier)
    async with PostgresUnitOfWork(engine) as uow:
        # RU/EN: Simulate passage of time, not a reschedule requiring reconfirmation.
        row = await uow.store.get(GigInvitation, identifier)
        event = await uow.store.get(Concert, row.concert_id)
        row.date = event.date = today() - timedelta(days=1)
    send = messenger(engine)
    view = await send(f"sf_view {identifier.hex}", private=True, chat=1)
    assert any("Посмотреть утверждённый" in label for label, _ in view.buttons)
    record = button(view, "Отметить: участвовал")
    assert "Готово" in (await send(record, private=True, chat=1)).text
    assert "Готово" in (await send(record, private=True, chat=1)).text
    async with PostgresUnitOfWork(engine) as uow:
        row = (await uow.store.find(StaffContact, user_id=world.admin.id))[0]
        assert row.worked_count == 1


async def test_large_lineup_and_invitation_lists_stay_within_telegram_limits(engine, world):
    from setlist_bot.domain.entities import GigSlot, User

    async with PostgresUnitOfWork(engine) as uow:
        role = await Staffing(uow.store, world.admin).role(world.group.id, "Г" * 100)
        people = [User(telegram_id=100 + i, display_name="М" * 200) for i in range(5)]
        for person in people:
            uow.store.add(person)
        await uow.store.flush()
        for position in range(1, 41):
            slot = GigSlot(
                group_id=world.group.id,
                concert_id=world.concert.id,
                role_id=role.id,
                position=position,
            )
            uow.store.add(slot)
            await uow.store.flush()
            for person in people:
                uow.store.add(
                    GigInvitation(
                        group_id=world.group.id,
                        concert_id=world.concert.id,
                        slot_id=slot.id,
                        user_id=person.id,
                        date=world.concert.date,
                        concert_epoch=world.concert.lifecycle_version,
                        expires_at=now() + timedelta(days=1),
                    )
                )
    send = messenger(engine)
    first = await send(f"sf_lineup {world.concert.id.hex}", private=True, chat=1)
    assert len(first.text) < 4096 and len(first.buttons) < 100
    assert "ещё 2" in first.text
    all_invites = await send(button(first, "Все приглашения"), private=True, chat=1)
    assert len([data for _, data in all_invites.buttons if data.startswith("sf_view ")]) == 5
    second = await send(button(first, "▶"), private=True, chat=1)
    assert "\n9." in second.text and "\n1." not in second.text
    assert len(second.text) < 4096 and len(second.buttons) < 100


async def test_replacement_expiry_and_reconfirmation_after_approval(engine, world):
    from setlist_bot.application.concert_details import ConcertDetails

    one, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, one, world.member.id)
    await reply(engine, world.member, source)
    async with PostgresUnitOfWork(engine) as uow:
        request = await Staffing(uow.store, world.member).request_replacement(source)
        request_id = request.id
    candidate = await invite(engine, world.admin, one, world.stranger.id, replacement_id=request_id)
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(StaffReplacement, request_id)).expires_at = now() - timedelta(
            seconds=1
        )
    with pytest.raises(AppError, match="input_expired"):
        await reply(engine, world.stranger, candidate)
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(StaffReplacement, request_id)).expires_at = now() + timedelta(days=1)
    await reply(engine, world.stranger, candidate)
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).approve_replacement(candidate, 2)
        await ConcertDetails(uow.store, world.admin).update(
            world.group.id, world.concert.id, "concert_date", "2027-02-03"
        )
    assert await reply(engine, world.stranger, candidate, expected=4) == ("confirmed", 5)
