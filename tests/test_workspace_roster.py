import asyncio
from uuid import UUID, uuid4

from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import GigInvitation, GigSlot, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def queue(client, session, action, **fields):
    payload = {**session, "action": action, "request_id": str(uuid4()), **fields}
    result = await post(client, 1, "roster_action", payload)
    assert result["status"] == "pending"
    return result["request"], payload


async def step(client, engine, session, action, *, succeeds=True, **fields):
    identifier, payload = await queue(client, session, action, **fields)
    assert await deliver_request(engine, identifier) is succeeds
    receipt = await get(client, 1, "catalog_status", **session, request=identifier)
    assert await post(client, 1, "roster_action", payload) == receipt
    return receipt


def scope(world):
    return {"band": str(world.group.id), "performance": str(world.concert2.id)}


async def prepare_two(client, engine, session, query):
    data = await get(client, 1, "performance_roster", **query)
    plan = await post(
        client,
        1,
        "staff_lineup_preview",
        {"band": query["band"], "name": "Состав", "quick": "Гитара: 2\nВокал: 1"},
    )
    await step(client, engine, session, "prepare", version=data["version"], rows=plan["rows"])
    return await get(client, 1, "performance_roster", **query)


async def test_review_rechecks_availability_requires_confirmation_and_sends_atomically(
    engine, world
):
    await setup_workspace(engine, world)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 1, "open_shared", query)
        await get(client, 2, "performance_roster", 403, **query)
        await get(
            client,
            1,
            "performance_roster",
            403,
            **{**query, "performance": str(world.other_concert.id)},
        )
        roster = await prepare_two(client, engine, session, query)
        assert roster["can_prepare"] and roster["can_send_defaults"]
        preview = await step(client, engine, session, "review", version=roster["version"])
        assert not preview.get("delivery")
        assert [row["status"] for row in preview["review"]] == ["unknown", "unknown"]
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(GigInvitation, concert_id=world.concert2.id)
            await Staffing(uow.store, world.member).busy(world.concert2.date, world.concert2.date)
        stale = await step(
            client, engine, session, "send", succeeds=False, preview=preview["request"], force=True
        )
        assert stale["error"] == "conflict"
        preview = await step(client, engine, session, "review", version=roster["version"])
        assert preview["review"][0] == {
            "role": "Гитара",
            "person": "2",
            "self": False,
            "status": "busy",
        }
        denied = await step(
            client, engine, session, "send", succeeds=False, preview=preview["request"]
        )
        assert denied["error"] == "staff_busy"
        other_session = await post(client, 1, "open_shared", query)
        wrong = await step(
            client,
            engine,
            other_session,
            "send",
            succeeds=False,
            preview=preview["request"],
            force=True,
        )
        assert wrong["error"] == "input_expired"
        sent = await step(client, engine, session, "send", preview=preview["request"], force=True)
        assert sent["recipients"] == 1 and sent["assigned_self"]
        stale = await step(
            client, engine, session, "send", succeeds=False, preview=preview["request"], force=True
        )
        assert stale["error"] == "input_expired"
        async with PostgresUnitOfWork(engine) as uow:
            rows = await uow.store.find(GigInvitation, concert_id=world.concert2.id)
            assert {row.user_id: row.status for row in rows} == {
                world.admin.id: "confirmed",
                world.member.id: "pending",
            }
            outbox = await uow.store.find(
                Outbox, concert_id=world.concert2.id, support_kind="staffing", chat_id=2
            )
            assert len(outbox) == 1
            member_row = next(row for row in rows if row.user_id == world.member.id)
            await Staffing(uow.store, world.member).respond(
                member_row.id, member_row.version, "yes", force=True
            )
        shown = await get(client, 2, "performance_roster", **query)
        assert [row["invitations"][0]["status"] for row in shown["slots"]] == [
            "confirmed",
            "confirmed",
        ]
        assert not shown["templates"] and all(slot["preferred"] is None for slot in shown["slots"])
        member = await post(client, 2, "open_shared", query)
        await post(
            client,
            2,
            "roster_action",
            {**member, "action": "review", "version": shown["version"], "request_id": str(uuid4())},
            403,
        )


async def test_template_candidates_reminder_and_cancel_use_existing_staffing(engine, world):
    await setup_workspace(engine, world)
    query = scope(world)
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Гитара")
        await staff.contact(world.group.id, role.id, world.member.id)
        template = await staff.template(world.group.id, "Дуэт", [{"role_id": str(role.id)}])
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 1, "open_shared", query)
        before = await get(client, 1, "performance_roster", **query)
        assert before["templates"][0]["name"] == "Дуэт"
        await step(
            client,
            engine,
            session,
            "apply_template",
            target=str(template.id),
            expected=template.version,
            version=before["version"],
        )
        prepared = await get(client, 1, "performance_roster", **query)
        slot = prepared["slots"][0]
        args = {"target": slot["id"], "expected": slot["version"], "version": prepared["version"]}
        found = await step(client, engine, session, "candidates", **args)
        assert [row["name"] for row in found["candidates"]] == ["2"]
        review = await step(
            client, engine, session, "review", **args, person=found["candidates"][0]["id"]
        )
        await step(client, engine, session, "send", preview=review["request"])
        current = await get(client, 1, "performance_roster", **query)
        assert not current["can_prepare"]
        row = current["slots"][0]["invitations"][0]
        assert row["status"] == "pending"
        invite = {"target": row["id"], "expected": row["version"], "version": current["version"]}
        await step(client, engine, session, "remind", **invite)
        await step(client, engine, session, "cancel", **invite)
        latest = await get(client, 1, "performance_roster", **query)
        role_args = {
            "target": slot["id"],
            "expected": slot["version"],
            "version": latest["version"],
        }
        await step(client, engine, session, "remove", **role_args)
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.get(GigSlot, UUID(slot["id"]))).removed
            assert (await uow.store.get(GigInvitation, UUID(row["id"]))).status == "cancelled"
            assert await uow.store.find(
                Outbox, support_kind="staffing", chat_id=2, concert_id=world.concert2.id
            )


async def test_worker_waits_for_people_before_locking_band(engine, world):
    await setup_workspace(engine, world)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 1, "open_shared", query)
        roster = await prepare_two(client, engine, session, query)
        review = await step(client, engine, session, "review", version=roster["version"])
        identifier, _ = await queue(client, session, "send", preview=review["request"])
        async with PostgresUnitOfWork(engine) as uow:
            await uow.store.lock_people({world.member.id})
            delivery = asyncio.create_task(deliver_request(engine, identifier))
            try:
                # The worker must wait for the person lock while the band stays available.
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
                    raise AssertionError("worker did not request the advisory lock")
                await asyncio.wait_for(Staffing(uow.store, world.admin).event(world.concert2.id), 2)
            except BaseException:
                delivery.cancel()
                await asyncio.gather(delivery, return_exceptions=True)
                raise
        assert await asyncio.wait_for(delivery, 4)
