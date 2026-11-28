import asyncio
from uuid import UUID, uuid4

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_staffing import invite, reply, setup_slots
from test_telegram import FakeTelegram
from test_workspace_roster import queue, step
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import GigInvitation, Outbox, StaffReplacement
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.staffing import invitation_view


async def replacement(client, engine, actor, session, *, ok=True, **command):
    body = {**session, "request_id": str(uuid4()), **command}
    queued = await post(client, actor, "gig_replacement", body)
    assert await deliver_request(engine, queued["request"]) is ok
    receipt = await get(client, actor, "catalog_status", **session, request=queued["request"])
    assert await post(client, actor, "gig_replacement", body) == receipt
    return receipt


async def roster_slot(client, world, slot_id):
    data = await get(
        client,
        1,
        "performance_roster",
        band=str(world.group.id),
        performance=str(world.concert.id),
    )
    return data["version"], next(row for row in data["slots"] if row["id"] == str(slot_id))


async def test_guest_requests_replacement_and_admin_self_assignment_updates_roster(engine, world):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, slot, world.stranger.id)
    _, version = await reply(engine, world.stranger, source)
    source_query = {"invitation": str(source)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        guest = await post(client, 3, "open_gig", source_query)
        await post(client, 2, "open_gig", source_query, 403)
        request = await replacement(client, engine, 3, guest, action="request", version=version)
        assert request["replacement_status"] == "pending"
        async with PostgresUnitOfWork(engine) as uow:
            controller = Controller(
                uow.store,
                world.admin,
                world.admin.telegram_id,
                private=True,
                miniapp_public_base_url="https://example.org",
            )
            notice = await invitation_view(controller, source, admin_notice=True)
            assert notice.buttons[0][1].startswith("sf_go ")
            shortcut = notice.web_apps[notice.buttons[-1][1]]
            assert f"focus=replacement-{request['replacement']['id']}" in shortcut
        assert (await get(client, 3, "gig_card", **source_query))["invitation"][
            "replacement"
        ] == request["replacement"]
        scoped = await post(
            client,
            1,
            "open_shared",
            {"band": str(world.group.id), "performance": str(world.concert.id)},
        )
        roster_version, role = await roster_slot(client, world, slot)
        original = role["invitations"][0]
        assert original["status"] == "confirmed" and original["needs_replacement"]
        assert original["request"] == request["replacement"]
        args = {
            "version": roster_version,
            "target": role["id"],
            "expected": role["version"],
            "replacement": original["request"]["id"],
        }
        candidates = await step(client, engine, scoped, "candidates", **args)
        assert all(row["id"] != str(world.stranger.id) for row in candidates["candidates"])
        preview = await step(
            client, engine, scoped, "review", **args, person=candidates["self"]["id"]
        )
        assert preview["replacement"] and preview["review"][0]["self"]
        queued, _ = await queue(client, scoped, "send", preview=preview["request"])

        # The real outbox must wait on the existing performer's lock before taking the band.
        async with PostgresUnitOfWork(engine) as uow:
            for event in await uow.store.find(Outbox, sent=False):
                if event.payload.get("request_id") != queued:
                    event.sent = True
        async with Bot("123456:TEST", session=FakeTelegram()) as bot:
            async with PostgresUnitOfWork(engine) as uow:
                await uow.store.lock_people({world.stranger.id})
                delivery = asyncio.create_task(deliver_one_outbox(bot, engine))
                try:
                    for _ in range(100):
                        if await uow.session.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_locks "
                                "WHERE locktype='advisory' AND NOT granted)"
                            )
                        ):
                            break
                        await asyncio.sleep(0.02)
                    else:
                        raise AssertionError("dispatcher did not wait for the original performer")
                    await asyncio.wait_for(
                        Staffing(uow.store, world.admin).event(world.concert.id), 2
                    )
                except BaseException:
                    delivery.cancel()
                    await asyncio.gather(delivery, return_exceptions=True)
                    raise
            assert await asyncio.wait_for(delivery, 4)

        assert (await get(client, 1, "catalog_status", **scoped, request=queued))[
            "replacement_confirmed"
        ]
        _, role = await roster_slot(client, world, slot)
        assert len(role["invitations"]) == 1
        row = role["invitations"][0]
        assert row["name"] == "1" and row["status"] == "confirmed" and row["substituted"]
        assert not row["replacement"] and not row["needs_replacement"]
        await get(client, 3, "gig_setlist", 403, **source_query)
        history = await get(client, 3, "my_gigs", section="history")
        assert history["invitations"][0]["status"] == "replaced"
        async with PostgresUnitOfWork(engine) as uow:
            notices = await uow.store.find(Outbox, chat_id=3, support_kind="staffing")
            assert any(item.payload["kind"] == "released" for item in notices)


async def test_candidate_requires_consent_and_admin_approval_and_old_preview_expires(engine, world):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, slot, world.member.id)
    _, version = await reply(engine, world.member, source)
    event = {"band": str(world.group.id), "performance": str(world.concert.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_shared", event)
        roster_version, _ = await roster_slot(client, world, slot)
        await step(
            client,
            engine,
            admin,
            "request_replacement",
            version=roster_version,
            target=str(source),
            expected=version,
        )
        roster_version, role = await roster_slot(client, world, slot)
        pending = role["invitations"][0]["request"]
        args = {
            "version": roster_version,
            "target": role["id"],
            "expected": role["version"],
            "replacement": pending["id"],
            "person": str(world.stranger.id),
            "reference": "3",
        }
        preview = await step(client, engine, admin, "review", **args)
        member = await post(client, 2, "open_gig", {"invitation": str(source)})
        await replacement(
            client,
            engine,
            2,
            member,
            action="cancel",
            version=version,
            replacement=pending["id"],
            expected=pending["version"],
        )
        expired = await step(
            client, engine, admin, "send", succeeds=False, preview=preview["request"]
        )
        assert expired["error"] == "input_expired"
        requested = await replacement(client, engine, 2, member, action="request", version=version)
        roster_version, _ = await roster_slot(client, world, slot)
        preview = await step(
            client,
            engine,
            admin,
            "review",
            **{**args, "version": roster_version, "replacement": requested["replacement"]["id"]},
        )
        await step(client, engine, admin, "send", preview=preview["request"])
        roster_version, role = await roster_slot(client, world, slot)
        original, candidate = role["invitations"]
        assert original["id"] == str(source) and original["status"] == "confirmed"
        assert candidate["replacement"] and candidate["status"] == "pending"
        early = await step(
            client,
            engine,
            admin,
            "approve_replacement",
            succeeds=False,
            version=roster_version,
            target=candidate["id"],
            expected=candidate["version"],
        )
        assert early["error"] == "input_expired"
        own = {"invitation": candidate["id"]}
        card = (await get(client, 3, "gig_card", **own))["invitation"]
        assert card["answers"] == ["yes", "no"]
        guest = await post(client, 3, "open_gig", own)
        queued = await post(
            client,
            3,
            "gig_reply",
            {**guest, "answer": "yes", "version": card["version"], "request_id": str(uuid4())},
        )
        assert await deliver_request(engine, queued["request"])
        roster_version, role = await roster_slot(client, world, slot)
        assert [r["status"] for r in role["invitations"]] == ["confirmed", "accepted"]
        nominee = role["invitations"][1]
        args = {"version": roster_version, "target": nominee["id"], "expected": nominee["version"]}
        ordinary = await post(client, 2, "open_shared", event)
        await post(
            client,
            2,
            "roster_action",
            {**ordinary, **args, "action": "approve_replacement", "request_id": str(uuid4())},
            403,
        )
        await step(client, engine, admin, "approve_replacement", **args)
        _, role = await roster_slot(client, world, slot)
        assert [r["id"] for r in role["invitations"]] == [candidate["id"]]
        assert (await get(client, 3, "gig_card", **own))["invitation"]["setlist"]


async def test_conditional_booking_can_request_and_cancel_replacement_in_other_band(engine, world):
    await setup_workspace(engine, world)
    first, second = await setup_slots(engine, world)
    source = await invite(engine, world.admin, first, world.stranger.id)
    await reply(engine, world.stranger, source)
    target = await invite(engine, world.admin, second, world.stranger.id, force=True)
    await reply(engine, world.stranger, target, "conditional")
    query = {"invitation": str(target)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        card = (await get(client, 3, "gig_card", **query))["invitation"]
        option = card["replacement_sources"][0]
        assert option["band"] == world.group.name and option["can_request"]
        source_session = await post(client, 3, "open_gig", {"invitation": option["id"]})
        target_session = await post(client, 3, "open_gig", query)
        stale = await replacement(
            client, engine, 3, target_session, ok=False, action="request", version=card["version"]
        )
        assert stale["error"] == "forbidden"
        created = await replacement(
            client,
            engine,
            3,
            source_session,
            action="request",
            version=option["version"],
            target=str(target),
        )
        linked = (await get(client, 3, "gig_card", **query))["invitation"]["replacement_sources"][0]
        assert linked["replacement"] == created["replacement"] and not linked["can_request"]
        await replacement(
            client,
            engine,
            3,
            source_session,
            action="cancel",
            version=option["version"],
            replacement=linked["replacement"]["id"],
            expected=linked["replacement"]["version"],
        )
        assert (await get(client, 3, "gig_card", **query))["invitation"]["replacement_sources"][0][
            "can_request"
        ]
        async with PostgresUnitOfWork(engine) as uow:
            row = await uow.store.get(StaffReplacement, UUID(linked["replacement"]["id"]))
            assert row.source_id == source and row.target_id == target and row.status == "cancelled"
            assert (await uow.store.get(GigInvitation, target)).status == "conditional"
