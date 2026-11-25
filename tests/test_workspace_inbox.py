from uuid import UUID

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_staffing import invite, reply, setup_slots
from test_workspace_replacements import replacement, roster_slot
from test_workspace_roster import step
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.setlists import Setlists
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    CatalogProposal,
    Concert,
    GigInvitation,
    StaffReplacement,
    User,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def test_readiness_includes_final_timing_and_optional_lineup(engine, world):
    await setup_workspace(engine, world)
    group = world.group.id
    event = world.concert.id
    scope = {"band": str(group), "performance": str(event)}
    async with PostgresUnitOfWork(engine) as uow:
        concert = await uow.store.get(Concert, event)
        concert.timing = {"rows": [{"start": "18:30", "label": "Сбор"}], "preliminary": True}
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(group, event, 0, ["Комета"])
        await lists.approve(group, event, 1)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        assert (await get(client, 2, "performance", **scope))["readiness"] is None
        initial = (await get(client, 1, "performance", **scope))["readiness"]
        assert initial["timing"] == "preliminary" and not initial["ready"]
        roster = await get(client, 1, "performance_roster", **scope)
        session = await post(client, 1, "open_shared", scope)
        await step(
            client, engine, session, "lineup_optional", version=roster["version"], required=False
        )
        draft = (await get(client, 1, "performance", **scope))["readiness"]
        assert draft["optional"] and not draft["ready"]
        async with PostgresUnitOfWork(engine) as uow:
            concert = await uow.store.get(Concert, event)
            concert.timing = {**concert.timing, "preliminary": False}
            for row in await uow.store.find(GigInvitation, concert_id=event):
                await Staffing(uow.store, world.member).request_replacement(row.id)
        waiting = (await get(client, 1, "performance", **scope))["readiness"]
        assert (
            waiting["timing"] == "ready" and waiting["replacements"] == 1 and not waiting["ready"]
        )
        async with PostgresUnitOfWork(engine) as uow:
            [row] = await uow.store.find(StaffReplacement, concert_id=event)
            await Staffing(uow.store, world.admin).cancel_request(row.id, row.version)
        assert (await get(client, 1, "performance", **scope))["readiness"]["ready"]
        updated = await get(client, 1, "performance_roster", **scope)
        await step(
            client, engine, session, "lineup_optional", version=updated["version"], required=True
        )
        assert not (await get(client, 1, "performance", **scope))["readiness"]["optional"]


async def test_unknown_candidate_link_is_private_single_use_and_updates_inbox(engine, world):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    source = await invite(engine, world.admin, slot, world.member.id)
    _, version = await reply(engine, world.member, source)
    scope = {"band": str(world.group.id), "performance": str(world.concert.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        member = await post(client, 2, "open_gig", {"invitation": str(source)})
        requested = await replacement(
            client, engine, 2, member, action="request", version=version, candidate="@NewStage"
        )
        pending = requested["replacement"]
        assert pending["candidate"] == "@newstage"
        queue = (await get(client, 1, "inbox"))["admin"]
        assert [(row["kind"], row["id"]) for row in queue] == [("replacement", pending["id"])]
        assert not (await get(client, 3, "inbox"))["admin"]
        admin = await post(client, 1, "open_shared", scope)
        roster_version, _ = await roster_slot(client, world, slot)
        result = await step(
            client,
            engine,
            admin,
            "replacement_link",
            version=roster_version,
            target=pending["id"],
            expected=pending["version"],
        )
        token = UUID(result["replacement_link"].removeprefix("swap_"))
        assert not (await get(client, 1, "inbox"))["admin"]
        async with PostgresUnitOfWork(engine) as uow:
            person = await uow.store.get(User, world.stranger.id)
            person.username = "different"
            with pytest.raises(AppError, match="input_expired"):
                await Staffing(uow.store, person).claim_replacement(token)
            person.username = "newstage"
            booking = await Staffing(uow.store, person).claim_replacement(token)
            await uow.store.flush()
            assert booking.status == "pending"
            with pytest.raises(AppError, match="input_expired"):
                await Staffing(uow.store, person).claim_replacement(token)
        personal = (await get(client, 3, "inbox"))["personal"]
        assert [(row["kind"], row["id"]) for row in personal] == [("gig", str(booking.id))]
        await reply(engine, world.stranger, booking.id)
        admin_items = (await get(client, 1, "inbox"))["admin"]
        assert [(row["kind"], row["id"]) for row in admin_items] == [
            ("approve_replacement", pending["id"])
        ]
        assert not (await get(client, 3, "inbox"))["personal"]


async def test_admin_inbox_filters_resolved_and_other_collectives(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        for group_id, state in ((world.group.id, "pending"), (world.other.id, "approved")):
            uow.store.add(
                CatalogProposal(
                    author_id=world.member.id,
                    group_id=group_id,
                    status=state,
                    payload={"title": "Комета"},
                )
            )
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        inbox = await get(client, 1, "inbox")
        assert [(row["kind"], row["band_id"]) for row in inbox["admin"]] == [
            ("catalog", str(world.group.id))
        ]
        assert not (await get(client, 2, "inbox"))["admin"]
