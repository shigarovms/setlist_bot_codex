"""Bookings, substitutions and privacy on real PostgreSQL; no live Telegram messages."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from setlist_bot.application.calendars import Calendars
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.groups import Groups
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    BusyPeriod,
    Concert,
    GigInvitation,
    GigSlot,
    Membership,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.calendar_queries import CalendarQueries
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def setup_slots(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        a = await service.role(world.group.id, "Гитара")
        b = await service.role(world.other.id, "Гитара")
        other = await uow.store.get(Concert, world.other_concert.id)
        other.date = world.concert.date
        one = await service.add_slot(world.concert.id, a.id)
        two = await service.add_slot(world.other_concert.id, b.id)
        return one.id, two.id


async def invite(engine, actor, slot_id, person_id, **kwargs):
    async with PostgresUnitOfWork(engine) as uow:
        slot = await uow.store.get(GigSlot, slot_id)
        row = await Staffing(uow.store, actor).invite(slot.id, person_id, slot.version, **kwargs)
        return row.id


async def reply(engine, person, identifier, action="yes", expected=1, **kwargs):
    async with PostgresUnitOfWork(engine) as uow:
        row = await Staffing(uow.store, person).respond(identifier, expected, action, **kwargs)
        return row.status, row.version


async def test_templates_and_contacts_do_not_grant_membership_or_reset_hidden_preference(
    engine, world
):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "Бас")
        assert (await service.role(world.group.id, "бас")).id == role.id
        await service.contact(world.group.id, role.id, world.stranger.id, "hidden")
        template = await service.template(
            world.group.id,
            "Малый",
            [
                {"role_id": str(role.id), "user_id": str(world.stranger.id)},
            ],
        )
        slots = await service.apply_template(world.concert.id, template.id, template.version)
        await uow.store.flush()
        assert not await service.candidates(slots[0].id)
        assert not await uow.store.find(Membership, user_id=world.stranger.id)
        with pytest.raises(AppError, match="staff_hidden"):
            await service.invite(slots[0].id, world.stranger.id, 1)
        with pytest.raises(AppError, match="staff_lineup_exists"):
            await service.apply_template(world.concert.id, template.id, template.version)


async def test_invitation_is_idempotent_and_conflicts_require_explicit_override(engine, world):
    one, two = await setup_slots(engine, world)
    first = await invite(engine, world.admin, one, world.member.id)
    assert first == await invite(engine, world.admin, one, world.member.id)
    assert await reply(engine, world.member, first) == ("confirmed", 2)
    assert await reply(engine, world.member, first) == ("confirmed", 2)
    with pytest.raises(AppError, match="staff_busy"):
        await invite(engine, world.admin, two, world.member.id)
    second = await invite(engine, world.admin, two, world.member.id, force=True)
    with pytest.raises(AppError, match="staff_busy"):
        await reply(engine, world.member, second)
    assert await reply(engine, world.member, second, force=True) == ("confirmed", 2)


async def test_simultaneous_cross_band_confirmations_cannot_silently_double_book(engine, world):
    one, two = await setup_slots(engine, world)
    ids = [await invite(engine, world.admin, s, world.member.id) for s in (one, two)]

    async def confirm(identifier):
        try:
            return await reply(engine, world.member, identifier)
        except AppError as error:
            return error.code

    result = await asyncio.wait_for(asyncio.gather(*(confirm(i) for i in ids)), 10)
    assert result.count("staff_busy") == 1 and result.count(("confirmed", 2)) == 1


async def test_substitution_keeps_original_until_candidate_accepts_and_admin_approves(
    engine, world
):
    one, two = await setup_slots(engine, world)
    source = await invite(engine, world.admin, one, world.member.id)
    await reply(engine, world.member, source)
    target = await invite(engine, world.admin, two, world.member.id, force=True)
    await reply(engine, world.member, target, "conditional")
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.member)
        request = await service.request_replacement(source, target)
        assert request.id == (await service.request_replacement(source, target)).id
        replacement_id = request.id
    candidate = await invite(
        engine, world.admin, one, world.stranger.id, replacement_id=replacement_id
    )
    assert await reply(engine, world.stranger, candidate) == ("accepted", 2)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, source)).status == "confirmed"
        assert (await uow.store.get(GigInvitation, target)).status == "conditional"
        with pytest.raises(AppError):
            await Staffing(uow.store, world.member).approve_replacement(candidate, 2)
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).approve_replacement(candidate, 2)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, source)).status == "replaced"
        assert (await uow.store.get(GigInvitation, candidate)).status == "confirmed"
        assert (await uow.store.get(GigInvitation, target)).status == "conditional"
        assert not await uow.store.find(Membership, user_id=world.stranger.id)
    assert await reply(engine, world.member, target, expected=2) == ("confirmed", 3)


async def test_date_change_invalidates_old_buttons_and_calendar_booking(engine, world):
    one, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, one, world.member.id)
    await reply(engine, world.member, identifier)
    async with PostgresUnitOfWork(engine) as uow:
        await ConcertDetails(uow.store, world.admin).update(
            world.group.id, world.concert.id, "concert_date", "2027-02-03"
        )
        row = await uow.store.get(GigInvitation, identifier)
        assert row.status == "reconfirm" and row.version == 3
        assert await Staffing(uow.store, world.admin).conflicts(world.member.id, row.date) == (0, 1)
    with pytest.raises(AppError, match="input_expired"):
        await reply(engine, world.member, identifier, expected=1)
    assert await reply(engine, world.member, identifier, expected=3) == ("confirmed", 4)


async def test_manual_busy_is_private_and_guest_only_reads_own_approved_setlist(engine, world):
    from test_exports import approved_export

    one, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, one, world.stranger.id)
    await reply(engine, world.stranger, identifier)
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.stranger).busy(world.concert.date, world.concert.date)
        assert await Staffing(uow.store, world.admin).conflicts(
            world.stranger.id, world.concert.date
        ) == (2, 0)
        periods = await uow.store.find(BusyPeriod, user_id=world.stranger.id)
        with pytest.raises(AppError, match="forbidden"):
            await Staffing(uow.store, world.member).unbusy(periods[0].id)
        await approved_export(uow, world)
    p = Processor(engine)
    guest = await p.process(1, 3, 3, f"sf_guest {identifier.hex}", private=True)
    assert "Комета" in guest.text
    pdf = await p.process(3, 3, 3, f"sf_pdf_l {identifier.hex}", private=True)
    assert pdf.files and pdf.files[0][1].startswith(b"%PDF")
    denied = await p.process(2, 2, 2, f"sf_guest {identifier.hex}", private=True)
    assert "Комета" not in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="forbidden"):
            await Groups(uow.store, world.stranger).access(world.group.id)


async def test_personal_subscription_is_separate_and_filters_guest_assignments(engine, world):
    from test_calendars import KEY as VAULT_KEY

    from setlist_bot.application.calendars import token_hash

    one, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, one, world.stranger.id)
    await reply(engine, world.stranger, identifier)
    async with PostgresUnitOfWork(engine) as uow:
        calendars = Calendars(uow.store, world.stranger, VAULT_KEY)
        personal, token = await calendars.create(None, "personal")
        all_groups, _ = await calendars.create(None)
        assert personal.id != all_groups.id and token
    _, events = await CalendarQueries(engine).read(token_hash(token))
    assert len(events) == 1 and events[0].id == world.concert.id
    assert events[0].route == "gig_" + identifier.hex
    async with PostgresUnitOfWork(engine) as uow:
        await Staffing(uow.store, world.admin).needed(one, 1, False)
    _, events = await CalendarQueries(engine).read(token_hash(token))
    assert events == ()


async def test_foreign_roles_and_non_admin_mutations_are_rejected(engine, world):
    one, two = await setup_slots(engine, world)
    with pytest.raises(AppError, match="admin_only"):
        await invite(engine, world.member, one, world.stranger.id)
    async with PostgresUnitOfWork(engine) as uow:
        role_id = (await uow.store.get(GigSlot, two)).role_id
        with pytest.raises(AppError, match="forbidden"):
            await Staffing(uow.store, world.admin).add_slot(world.concert.id, role_id)
    identifier = await invite(engine, world.admin, one, world.member.id)
    with pytest.raises(AppError, match="forbidden"):
        await reply(engine, world.stranger, identifier)


async def test_expired_and_cancelled_invitations_cannot_be_accepted(engine, world):
    one, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, one, world.member.id)
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(GigInvitation, identifier)).expires_at = now() - timedelta(seconds=1)
    with pytest.raises(AppError, match="input_expired"):
        await reply(engine, world.member, identifier)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(GigInvitation, identifier)
        row.expires_at = now() + timedelta(days=1)
        await Staffing(uow.store, world.admin).needed(one, 1, False)
    with pytest.raises(AppError):
        await reply(engine, world.member, identifier)


async def test_schema_has_tenant_constraints_and_migration_does_not_erase_bookings(
    engine, world, database_url
):
    from test_migrations import alembic

    await setup_slots(engine, world)
    result = alembic(database_url, "downgrade", "f2d7c614a083", check=False)
    assert result.returncode != 0 and "would lose staffing data" in result.stderr
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM gig_slots")) == 2


async def test_bulk_default_invites_warn_before_commit_and_override_is_explicit(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        first = await service.role(world.group.id, "Вокал")
        second = await service.role(world.group.id, "Звукорежиссёр")
        template = await service.template(
            world.group.id,
            "Малый",
            [
                {"role_id": str(first.id), "user_id": str(world.member.id)},
                {"role_id": str(second.id), "user_id": str(world.stranger.id)},
            ],
        )
        slots = await service.apply_template(world.concert.id, template.id, template.version)
        versions = {str(slot.id): slot.version for slot in slots}
        await Staffing(uow.store, world.stranger).busy(world.concert.date, world.concert.date)
    with pytest.raises(AppError, match="staff_busy"):
        async with PostgresUnitOfWork(engine) as uow:
            await Staffing(uow.store, world.admin).invite_defaults(world.concert.id, versions)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(GigInvitation)
        await Staffing(uow.store, world.admin).invite_defaults(
            world.concert.id, versions, force=True
        )
        await uow.store.flush()
        assert len(await uow.store.find(GigInvitation)) == 2


@pytest.mark.parametrize("scope", ["concert", "group"])
async def test_deletion_and_purge_cancel_pending_substitution_without_touching_other_band(
    engine, world, scope
):
    from setlist_bot.application.trash import Trash, TrashCleanup
    from setlist_bot.domain.entities import StaffReplacement, StaffRole, User

    one, two = await setup_slots(engine, world)
    source = await invite(engine, world.admin, one, world.member.id)
    await reply(engine, world.member, source)
    other = await invite(engine, world.admin, two, world.member.id, force=True)
    await reply(engine, world.member, other, "conditional")
    async with PostgresUnitOfWork(engine) as uow:
        request = await Staffing(uow.store, world.member).request_replacement(source, other)
        request_id = request.id
    candidate = await invite(engine, world.admin, one, world.stranger.id, replacement_id=request_id)
    await reply(engine, world.stranger, candidate)
    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        if scope == "group":
            target = await trash.delete_group(
                world.group.id, await trash.group_fingerprint(world.group.id)
            )
        else:
            target = await trash.delete_concert(
                world.group.id,
                world.concert.id,
                await trash.concert_fingerprint(world.group.id, world.concert.id),
            )
        assert (await uow.store.get(GigInvitation, source)).status == "cancelled"
        assert (await uow.store.get(GigInvitation, candidate)).status == "cancelled"
        assert (await uow.store.get(StaffReplacement, request_id)).status == "cancelled"
        target.deleted_at = now() - timedelta(days=31)
        await uow.store.flush()
        await TrashCleanup(uow.store).run()
        assert not await uow.store.find(GigInvitation, concert_id=world.concert.id)
        assert not await uow.store.find(StaffReplacement, id=request_id)
        assert (await uow.store.get(GigInvitation, other)).status == "conditional"
        assert len(await uow.store.find(StaffRole, group_id=world.other.id)) == 1
        assert len(await uow.store.find(User)) == 3


async def test_filling_a_slot_cancels_other_pending_invitations(engine, world):
    one, _ = await setup_slots(engine, world)
    first = await invite(engine, world.admin, one, world.member.id)
    second = await invite(engine, world.admin, one, world.stranger.id)
    await reply(engine, world.member, first)
    with pytest.raises(AppError, match="input_expired"):
        await reply(engine, world.stranger, second)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(GigInvitation, first)).status == "confirmed"
        assert (await uow.store.get(GigInvitation, second)).status == "cancelled"
