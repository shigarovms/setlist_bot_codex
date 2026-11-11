"""Consent, access isolation and replacement completion across the unified team."""

import pytest
from test_staffing import invite, reply, setup_slots

from setlist_bot.application.groups import Groups
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.team import Team
from setlist_bot.domain.entities import GigInvitation, Membership, StaffContact, StaffReplacement
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.staffing_lineups import event_lineup


async def test_accept_team_invitation_grants_membership_and_all_roles_without_register(
    engine, world
):
    async with PostgresUnitOfWork(engine) as uow:
        row = await Team(uow.store, world.admin).invite(
            world.group.id, world.stranger.id, ["Менеджер", "Световик"]
        )
        identifier, version = row.id, row.version
        assert row.status == "invited"
        assert not await uow.store.find(
            StaffContact, group_id=world.group.id, user_id=world.stranger.id, permanent=True
        )
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="forbidden"):
            await Groups(uow.store, world.stranger).access(world.group.id)
    async with PostgresUnitOfWork(engine) as uow:
        row = await Team(uow.store, world.stranger).respond(identifier, version, True)
        assert row.status == "active" and row.role == "member"
        assert (
            len(await uow.store.find(StaffContact, user_id=world.stranger.id, permanent=True)) == 2
        )
        response = await Controller(uow.store, world.stranger, 3, private=True).execute("groups")
        assert world.group.name in [label for label, _ in response.buttons]
        assert world.other.name not in [label for label, _ in response.buttons]
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="input_expired"):
            await Team(uow.store, world.stranger).respond(identifier, version, True)


async def test_new_roles_need_consent_and_decline_preserves_membership(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "Гитара")
        await service.set_permanent(world.group.id, role.id, world.member.id)
        row = await Team(uow.store, world.admin).invite(
            world.group.id, world.member.id, ["Световик"]
        )
        assert row.status == "active"
        assert len(await uow.store.find(StaffContact, user_id=world.member.id, permanent=True)) == 1
        await Team(uow.store, world.member).respond(row.id, row.version, False)
        assert row.status == "active"
        assert len(await uow.store.find(StaffContact, user_id=world.member.id, permanent=True)) == 1


async def test_revoked_invitation_sender_cannot_grant_access(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        await Groups(uow.store, world.admin).administrator(world.group.id, member.id, True)
        row = await Team(uow.store, world.member).invite(world.group.id, world.stranger.id, ["Бас"])
        identifier, version = row.id, row.version
        await Groups(uow.store, world.admin).administrator(world.group.id, member.id, False)
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="input_expired"):
            await Team(uow.store, world.stranger).respond(identifier, version, True)


async def test_template_does_not_grant_membership_or_permanent_role(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "Бас")
        await service.template(
            world.group.id, "Гости", [{"role_id": str(role.id), "user_id": str(world.stranger.id)}]
        )
        assert not await uow.store.find(
            Membership, group_id=world.group.id, user_id=world.stranger.id
        )
        assert not await uow.store.find(
            StaffContact, group_id=world.group.id, user_id=world.stranger.id, permanent=True
        )


async def test_leave_keeps_bookings_and_history_but_revokes_band_access(engine, world):
    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.member.id)
    await reply(engine, world.member, identifier, "yes")
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        await Team(uow.store, world.member).remove(member.id, member.version)
        assert (await uow.store.get(GigInvitation, identifier)).status == "confirmed"
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="forbidden"):
            await Groups(uow.store, world.member).access(world.group.id)
        owner = (await uow.store.find(Membership, group_id=world.group.id, user_id=world.admin.id))[
            0
        ]
        with pytest.raises(AppError, match="team_transfer_first"):
            await Team(uow.store, world.admin).remove(owner.id, owner.version)


async def test_self_substitution_finishes_and_lineup_shows_new_performer(engine, world):
    slot, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, slot, world.member.id)
    await reply(engine, world.member, source, "yes")
    async with PostgresUnitOfWork(engine) as uow:
        request = await Staffing(uow.store, world.member).request_replacement(source)
        request_id = request.id
        screen = await event_lineup(
            Controller(uow.store, world.admin, 1, private=True), world.concert.id
        )
        assert "запросил подмену" in screen.text
    candidate = await invite(engine, world.admin, slot, world.admin.id, replacement_id=request_id)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, source)).status == "replaced"
        assert (await uow.store.get(GigInvitation, candidate)).status == "confirmed"
        assert (await uow.store.get(StaffReplacement, request_id)).status == "approved"
        screen = await event_lineup(
            Controller(uow.store, world.admin, 1, private=True), world.concert.id
        )
        assert "запросил подмену" not in screen.text
        assert world.group.name in screen.text


async def test_other_substitute_still_needs_approval_and_is_visible(engine, world):
    slot, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, slot, world.member.id)
    await reply(engine, world.member, source, "yes")
    async with PostgresUnitOfWork(engine) as uow:
        request = await Staffing(uow.store, world.member).request_replacement(source)
        request_id = request.id
    candidate = await invite(
        engine, world.admin, slot, world.stranger.id, replacement_id=request_id
    )
    await reply(engine, world.stranger, candidate, "yes")
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, source)).status == "confirmed"
        screen = await event_lineup(
            Controller(uow.store, world.admin, 1, private=True), world.concert.id
        )
        assert "ожидает утверждения" in screen.text
        assert any("Утвердить подмену" in label for label, _ in screen.buttons)


async def test_crossed_application_and_invitation_need_no_second_consent(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        row = await Team(uow.store, world.admin).invite(
            world.group.id, world.stranger.id, ["Басист"]
        )
        names = list(row.invitation["roles"])
        member = await Groups(uow.store, world.stranger).request_roles(world.group.id, names)
        assert (
            member.status == "active"
            and member.invitation is None
            and member.requested_roles is None
        )
    async with PostgresUnitOfWork(engine) as uow:
        member = await Groups(uow.store, world.stranger).request_roles(world.other.id, ["Световик"])
        names = list(member.requested_roles)
        accepted = await Team(uow.store, world.admin).invite(
            world.other.id, world.stranger.id, names
        )
        assert accepted.status == "active" and accepted.invitation is None


async def test_removed_role_does_not_revoke_membership_or_change_profile(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Басист")
        contact = await staff.set_permanent(world.group.id, role.id, world.member.id)
        await staff.change_contact(contact.id, contact.version, remove=True)
        assert (await Groups(uow.store, world.member).access(world.group.id)).id == world.group.id
        assert not contact.permanent
        assert world.member.profile == {}


async def test_legacy_team_migration_preserves_roles_priorities_and_bookings(
    engine, world, database_url
):
    from sqlalchemy import text
    from test_migrations import alembic

    slot, _ = await setup_slots(engine, world)
    booking = await invite(engine, world.admin, slot, world.stranger.id)
    await reply(engine, world.stranger, booking, "yes")
    alembic(database_url, "downgrade", "c91a72e6b304")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE staff_contacts SET permanent=true,priority=7 WHERE user_id=:person"),
            {"person": world.stranger.id},
        )
    alembic(database_url, "upgrade", "head")
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.stranger.id)
        )[0]
        assert member.status == "invited" and member.invitation["legacy"]
        assert (await uow.store.get(GigInvitation, booking)).status == "confirmed"
        assert not await uow.store.find(StaffContact, user_id=world.stranger.id, permanent=True)
        await Team(uow.store, world.stranger).respond(member.id, member.version, True)
        contacts = await uow.store.find(StaffContact, user_id=world.stranger.id, permanent=True)
        assert len(contacts) == 1 and contacts[0].priority == 7


async def test_team_delivery_rechecks_version_recipient_and_group_lifecycle(engine, world):
    from aiogram import Bot
    from test_telegram import FakeTelegram

    from setlist_bot.domain.entities import Group, Outbox
    from setlist_bot.infrastructure.team_delivery import deliver_team

    async with PostgresUnitOfWork(engine) as uow:
        member = await Team(uow.store, world.admin).invite(
            world.group.id, world.stranger.id, ["Менеджер", "Световик"]
        )
        identifier, version = member.id, member.version
        event_id = (await uow.store.find(Outbox, support_kind="team"))[0].id
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        async with PostgresUnitOfWork(engine) as uow:
            assert await deliver_team(bot, uow, await uow.store.get(Outbox, event_id))
        assert len(session.sent) == 1
        assert "Приглашение в постоянную команду" in session.sent[0].text
        assert session.sent[0].chat_id == 3
        assert session.sent[0].reply_markup.inline_keyboard[0][0].text == "Вступить в коллектив"
        async with PostgresUnitOfWork(engine) as uow:
            with pytest.raises(AppError, match="forbidden"):
                await Team(uow.store, world.member).respond(identifier, version, True)
        async with PostgresUnitOfWork(engine) as uow:
            group = await uow.store.get(Group, world.group.id)
            group.lifecycle_version += 1
        async with PostgresUnitOfWork(engine) as uow:
            with pytest.raises(AppError, match="input_expired"):
                await Team(uow.store, world.stranger).respond(identifier, version, True)


async def test_cancelled_team_invitation_cannot_be_accepted(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        row = await Team(uow.store, world.admin).invite(
            world.group.id, world.stranger.id, ["Басист"]
        )
        identifier, version = row.id, row.version
        await Team(uow.store, world.admin).cancel(identifier, version)
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="input_expired"):
            await Team(uow.store, world.stranger).respond(identifier, version, True)
        assert not await uow.store.find(Membership, user_id=world.stranger.id, status="active")
