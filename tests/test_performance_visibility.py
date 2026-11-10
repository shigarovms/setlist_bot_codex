import pytest
from conftest import invite_fixture_member
from test_calendars import client_for, issue
from test_setlist_menus import sender

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Concert, GigInvitation, Membership
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def test_member_and_admin_in_different_bands_old_buttons_and_invitation_history(
    engine, world
):
    send = sender(engine)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(
            Membership(
                group_id=world.other.id, user_id=world.member.id, role="admin", status="active"
            )
        )
    schedule = await send("/my_gigs", actor=2, private=True)
    assert ("🎸 Выступления моих коллективов", "all_my_concerts admin") in schedule.buttons
    first = await send("/all_my_concerts", actor=2, private=True)
    assert [action for _, action in first.buttons] == [f"my_concert {world.other_concert.id.hex}"]
    group = await send(f"group_concerts {world.group.id.hex}", actor=2, private=True)
    assert not any(
        action == "concert" or str(world.concert.id) in action for _, action in group.buttons
    )
    async with PostgresUnitOfWork(engine) as uow:
        invitation = await invite_fixture_member(
            uow.store, world.concert, world.member, status="pending"
        )
    old = f"my_concert {world.concert.id.hex}"
    opened = await send(old, actor=2, private=True)
    assert opened.menu_card_id
    menu = await send("/all_my_concerts admin", actor=2, private=True)
    assert old not in [action for _, action in menu.buttons]
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(GigInvitation, invitation.id)
        row.status = "cancelled"
    denied = await send(old, actor=2, private=True)
    assert not denied.buttons and "Новый год" not in denied.text
    stale = await send(f"slmenu {opened.menu_card_id.hex} options", actor=2, private=True)
    assert stale.callback_notice and not stale.buttons
    history = await send("my_gigs history", actor=2, private=True)
    assert "отменено" in history.text
    assert not any("sf_view" in action for _, action in history.buttons)


async def test_session_performer_access_and_existing_calendar_link_follow_date_and_epoch(
    engine, world
):
    _, token, _ = await issue(engine, world.stranger)
    path = f"/calendar/{token}.ics"
    async with client_for(engine) as client:
        assert "BEGIN:VEVENT" not in await (await client.get(path)).text()
        async with PostgresUnitOfWork(engine) as uow:
            invitation = await invite_fixture_member(uow.store, world.concert, world.stranger)
        updated = await (await client.get(path)).text()
        assert "Новый год" in updated and "Второй концерт" not in updated
        send = sender(engine)
        session_view = await send(
            f"/start performance_{world.concert.id.hex}", actor=3, private=True
        )
        assert "Новый год" in session_view.text and not session_view.menu_card_id
        async with PostgresUnitOfWork(engine) as uow:
            event = await uow.store.get(Concert, world.concert.id)
            event.lifecycle_version += 1
        denied = await send(f"sf_guest {invitation.id.hex}", actor=3, private=True)
        assert not denied.buttons and "Новый год" not in denied.text
        assert "BEGIN:VEVENT" not in await (await client.get(path)).text()


async def test_history_menu_and_old_history_index_recheck_access(engine, world):
    from datetime import timedelta

    from setlist_bot.domain.concerts import today

    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        first = await lists.create(world.group.id, today() - timedelta(days=3), "Первое")
        hidden = await lists.create(world.group.id, today() - timedelta(days=1), "Недоступное")
        for event in (first, hidden):
            await lists.edit_songs(world.group.id, event.id, 0, ["Комета"])
            await lists.approve(world.group.id, event.id, 1)
        invite = await invite_fixture_member(uow.store, first, world.member)
        await invite_fixture_member(uow.store, world.concert, world.member)
        mini = MiniDrafts(uow.store, world.member)
        session = await mini.create(world.group.id, world.concert.id)
        await uow.store.flush()
        menu = await mini.history_menu(session.id)
        assert len(menu) == 1 and "Первое" in menu[0]["label"]
        assert (await mini.history(session.id, menu[0]["index"]))["kind"] == "recommendation"
        (await uow.store.get(GigInvitation, invite.id)).status = "cancelled"
        await uow.store.flush()
        assert not await mini.history_menu(session.id)
        with pytest.raises(AppError, match="miniapp_no_history"):
            await mini.history(session.id, menu[0]["index"])
