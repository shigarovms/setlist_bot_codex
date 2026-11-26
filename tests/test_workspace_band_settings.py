from datetime import date, datetime
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_collective_workflows import client as telegram_client
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application import reminders
from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Membership, Outbox, Reminder, User
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app

OP = "band_settings_save"


def request(session, expected, changes):
    return {**session, "request_id": str(uuid4()), "changes": changes, "expected": expected}


async def process(client, engine, actor, session, queued, *, ok=True):
    assert queued["status"] == "pending"
    assert await deliver_request(engine, queued["request"]) is ok
    receipt = await get(client, actor, "catalog_status", **session, request=queued["request"])
    assert receipt["status"] == ("saved" if ok else "failed")
    return receipt


async def test_band_settings_reuse_telegram_rules_and_leave_existing_setlists_intact(engine, world):
    await setup_workspace(engine, world)
    send = telegram_client(engine)
    initial_card = await send(f"/use_concert {world.concert.id}", remember=40)
    assert initial_card.monospace
    band = str(world.group.id)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        initial = await get(client, 1, "band_settings", band=band)
        assert initial["admin"] and initial["band"] == world.group.name
        member = await get(client, 2, "band_settings", band=band)
        assert not member["admin"]
        assert member["settings"] == {"setlist_style": "mono", "admins_only_suggestions": False}
        await get(client, 3, "band_settings", 403, band=band)
        await get(client, 2, "band_settings", 403, band=str(world.other.id))
        session = await post(client, 1, "open_repertoire", {"band": band})
        changes = {
            "setlist_style": "article",
            "admins_only_suggestions": True,
            "default_pause_seconds": 14,
            "language": "en",
            "reminders_enabled": False,
            "timing_coordinator_id": str(world.member.id),
        }
        payload = request(session, initial["settings"], changes)
        queued = await post(client, 1, OP, payload)
        assert await post(client, 1, OP, payload) == queued
        receipt = await process(client, engine, 1, session, queued)
        assert await post(client, 1, OP, payload) == receipt
        updated = await get(client, 1, "band_settings", band=band)
        assert updated["language"] == "ru" and updated["settings"] == changes
        other = await get(client, 1, "band_settings", band=str(world.other.id))
        assert other["settings"] == initial["settings"]
        member_view = await get(client, 2, "performances", band=band)
        assert not member_view["can_edit_draft"]
    async with PostgresUnitOfWork(engine) as uow:
        refreshes = await uow.store.find(Outbox, group_id=world.group.id, support_kind="refresh")
        assert [event.payload["card_id"] for event in refreshes] == [str(initial_card.menu_card_id)]
        lists = Setlists(uow.store, world.admin)
        old, version, approved = await lists.read(world.group.id, world.concert.id)
        assert old["default_pause_seconds"] == 5 and version == 0 and not approved
        concert = await lists.create(world.group.id, date(2026, 12, 31), "Новая пауза")
        new, _, _ = await lists.read(world.group.id, concert.id)
        assert new["default_pause_seconds"] == 14
        await reminders.queue(
            uow.store, concert.id, datetime(2026, 12, 30, 16, tzinfo=reminders.MOSCOW)
        )
        assert not await uow.store.find(Reminder, concert_id=concert.id)
        assert (await uow.store.get(User, world.admin.id)).export_settings == {}
    current_card = await send(f"/use_concert {world.concert.id}")
    assert current_card.articles
    assert "Reminders disabled" in (await send("/reminders")).text


async def test_partial_settings_conflicts_preserve_concurrent_telegram_edits(engine, world):
    await setup_workspace(engine, world)
    send = telegram_client(engine)
    band = str(world.group.id)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 1, "open_repertoire", {"band": band})
        payload = request(
            session,
            {"default_pause_seconds": 5, "reminders_enabled": True},
            {"default_pause_seconds": 14, "reminders_enabled": False},
        )
        queued = await post(client, 1, OP, payload)
        await send("/group_pause 9")
        receipt = await process(client, engine, 1, session, queued, ok=False)
        assert receipt["error"] == "conflict"
        actual = (await get(client, 1, "band_settings", band=band))["settings"]
        assert actual["default_pause_seconds"] == 9 and actual["reminders_enabled"]

        for key, expected, value in [
            ("default_pause_seconds", 9, 86401),
            ("language", "ru", "de"),
            ("reminders_enabled", True, 0),
            ("owner_id", None, None),
        ]:
            await post(client, 1, OP, request(session, {key: expected}, {key: value}), 403)
        payload = request(session, {"language": "ru"}, {"language": "en"})
        queued = await post(client, 1, OP, payload)
        await send("/reminders off")
        await process(client, engine, 1, session, queued)
        final = (await get(client, 1, "band_settings", band=band))["settings"]
        assert final["language"] == "en" and not final["reminders_enabled"]
        assert final["default_pause_seconds"] == 9


async def test_worker_rechecks_admin_and_only_uses_band_session(engine, world):
    await setup_workspace(engine, world)
    band = str(world.group.id)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, user_id=world.member.id, group_id=world.group.id)
        )[0]
        member_id = member.id
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 2, "open_repertoire", {"band": band})
        payload = request(session, {"reminders_enabled": True}, {"reminders_enabled": False})
        await post(client, 1, OP, payload, 401)
        denied = await post(client, 2, OP, payload, 403)
        assert denied["error"] == "admin_only"
        await post(client, 3, "open_repertoire", {"band": band}, 403)
        async with PostgresUnitOfWork(engine) as uow:
            await Groups(uow.store, world.admin).administrator(world.group.id, member_id, True)
        account = {"session": (await post(client, 2, "open_account", {}))["session"]}
        await post(
            client,
            2,
            OP,
            request(account, {"reminders_enabled": True}, {"reminders_enabled": False}),
            403,
        )
        queued = await post(client, 2, OP, payload)
        async with PostgresUnitOfWork(engine) as uow:
            await Groups(uow.store, world.admin).administrator(world.group.id, member_id, False)
        receipt = await process(client, engine, 2, session, queued, ok=False)
        assert receipt["error"] == "admin_only"
        assert (await get(client, 1, "band_settings", band=band))["settings"]["reminders_enabled"]
