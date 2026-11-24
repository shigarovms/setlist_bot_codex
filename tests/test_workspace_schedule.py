from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from test_calendars import client_for
from test_miniapp import KEY, settings
from test_repertoire_workspace_actions import get, post
from test_staffing import invite, reply, setup_slots
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.calendars import Calendars
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.workspace_schedule import CalendarRuntime
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import BusyPeriod, CalendarSubscription, Confirmation, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.miniapp_catalog_delivery import deliver_catalog
from setlist_bot.miniapp.app import create_app


async def action(client, engine, actor, session, action, *, runtime=None, ok=True, **fields):
    payload = {**session, "action": action, "request_id": str(uuid4()), **fields}
    queued = await post(client, actor, "schedule_action", payload)
    assert queued["status"] == "pending"
    async with PostgresUnitOfWork(engine) as uow:
        outbox = await uow.store.find(Outbox, support_kind="catalog", sent=False)
        event = next(row for row in outbox if row.payload["request_id"] == queued["request"])
        assert event.group_id is None and event.concert_id is None
        assert await deliver_catalog(uow, event, calendar=runtime) is ok
    receipt = await get(client, actor, "catalog_status", **session, request=queued["request"])
    assert await post(client, actor, "schedule_action", payload) == receipt
    return receipt


async def test_schedule_groups_roles_and_lists_organized_events_only_for_admin(engine, world):
    await setup_workspace(engine, world)
    first, _ = await setup_slots(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Вокал")
        second = (await staff.add_slot(world.concert.id, role.id)).id
    for slot in (first, second):
        invitation = await invite(engine, world.admin, slot, world.stranger.id)
        await reply(engine, world.stranger, invitation)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        scheduled = await get(client, 3, "my_gigs", section="schedule")
        assert not scheduled["can_manage"] and len(scheduled["invitations"]) == 1
        row = scheduled["invitations"][0]
        assert row["title"] == world.concert.title
        assert {row["role"], row["other_roles"][0]["role"]} == {"Гитара", "Вокал"}
        assert await get(client, 3, "my_gigs", section="managed") == {
            "language": "ru",
            "can_manage": False,
            "has_next": False,
            "managed": [],
        }
        managed = await get(client, 1, "my_gigs", section="managed")
        assert managed["can_manage"]
        assert {row["band_id"] for row in managed["managed"]} == {
            str(world.group.id),
            str(world.other.id),
        }


async def test_busy_periods_are_private_and_reuse_telegram_conflicts_and_notices(engine, world):
    await setup_workspace(engine, world)
    event_day = world.concert.date
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        member = {"session": (await post(client, 2, "open_schedule", {}))["session"]}
        admin = {"session": (await post(client, 1, "open_schedule", {}))["session"]}
        await post(client, 1, "schedule_action", {**member, "action": "busy_list"}, 401)
        await get(client, 2, "song", 403, **member, song_id=str(world.song.id))
        info = await action(client, engine, 2, member, "busy_add", starts_on=event_day.isoformat())
        assert info["status"] == "saved"
        rows = (await action(client, engine, 2, member, "busy_list"))["periods"]
        assert rows[0]["starts_on"] == rows[0]["ends_on"] == event_day.isoformat()
        assert not (await action(client, engine, 1, admin, "busy_list"))["periods"]
        foreign = await action(
            client, engine, 1, admin, "busy_remove", ok=False, period=rows[0]["id"]
        )
        assert foreign["error"] == "forbidden"
        async with PostgresUnitOfWork(engine) as uow:
            counts = await Staffing(uow.store, world.admin).conflicts(
                world.member.id, event_day, exclude=world.concert.id
            )
            assert counts == (1, 0)
            notices = await uow.store.find(Outbox, support_kind="staffing", group_id=world.group.id)
            assert any(event.payload["kind"] == "busy" for event in notices)
        past = await action(
            client,
            engine,
            2,
            member,
            "busy_add",
            ok=False,
            starts_on=(today() - timedelta(days=1)).isoformat(),
        )
        assert past["error"] == "input"
        await action(client, engine, 2, member, "busy_remove", period=rows[0]["id"])
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(BusyPeriod, user_id=world.member.id)
        assert not (await action(client, engine, 2, member, "calendar_list"))["enabled"]


async def test_guest_calendar_reveal_rotate_disable_and_restore_use_existing_subscription(
    engine, world
):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    invitation = await invite(engine, world.admin, slot, world.stranger.id)
    await reply(engine, world.stranger, invitation)
    runtime = CalendarRuntime("https://example.org", Fernet.generate_key().decode(), KEY)
    async with (
        TestClient(TestServer(create_app(settings(engine), engine))) as client,
        client_for(engine) as feed,
    ):
        guest = {"session": (await post(client, 3, "open_schedule", {}))["session"]}
        scopes = await action(client, engine, 3, guest, "calendar_list", runtime=runtime)
        assert [row["scope"] for row in scopes["scopes"]] == ["personal", "all"]
        foreign_scope = await action(
            client,
            engine,
            3,
            guest,
            "calendar_create",
            runtime=runtime,
            ok=False,
            scope=str(world.group.id),
            version=None,
        )
        assert foreign_scope["error"] == "forbidden"
        created = await action(
            client,
            engine,
            3,
            guest,
            "calendar_create",
            runtime=runtime,
            scope="personal",
            version=None,
        )
        url = created["url"]
        old_path = urlsplit(url).path
        response = await feed.get(old_path)
        body = await response.text()
        assert response.status == 200 and "Коллектив А" in body and "Коллектив Б" not in body
        assert body.count("BEGIN:VEVENT") == 1
        listing = await action(client, engine, 3, guest, "calendar_list", runtime=runtime)
        current = listing["scopes"][0]
        target = {"subscription": current["id"], "version": current["version"]}
        async with PostgresUnitOfWork(engine) as uow:
            subscription = await uow.store.get(CalendarSubscription, UUID(current["id"]))
            assert (
                await Calendars(uow.store, world.stranger, runtime.link_key).reveal(subscription.id)
            ) == old_path.split("/")[-1].removesuffix(".ics")
            ciphertext = subscription.token_ciphertext
            subscription.token_ciphertext = ""  # Legacy rows could only store the hash.
            receipt = await uow.store.get(Confirmation, UUID(created["request"]))
            assert old_path not in receipt.command and ciphertext not in receipt.command
        legacy = await action(
            client, engine, 3, guest, "calendar_reveal", runtime=runtime, **target
        )
        assert legacy["legacy"]
        restored = await action(
            client, engine, 3, guest, "calendar_restore", runtime=runtime, url=url, **target
        )
        assert restored["url"] == url
        async with PostgresUnitOfWork(engine) as uow:
            receipt = await uow.store.get(Confirmation, UUID(restored["request"]))
            assert url not in receipt.command
        rotated = await action(
            client, engine, 3, guest, "calendar_rotate", runtime=runtime, **target
        )
        new_path = urlsplit(rotated["url"]).path
        assert new_path != old_path
        assert (await feed.get(old_path)).status == 404 and (await feed.get(new_path)).status == 200
        disabled = await action(
            client,
            engine,
            3,
            guest,
            "calendar_disable",
            runtime=runtime,
            **{**target, "version": target["version"] + 1},
        )
        assert disabled["status"] == "saved" and (await feed.get(new_path)).status == 404
