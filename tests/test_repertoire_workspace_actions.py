from uuid import UUID, uuid4

import pytest
from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings, signed
from test_miniapp_catalog import payload
from test_telegram import FakeTelegram

from setlist_bot.domain.entities import DeploymentIdentity, Group, Membership, Outbox, PersonalDraft
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app
from setlist_bot.transport.controller import Controller


def headers(actor):
    return {"Authorization": "tma " + signed(actor=actor), "Origin": "https://example.org"}


async def post(client, actor, operation, data, status=200):
    response = await client.post(f"/miniapp/api/{operation}", json=data, headers=headers(actor))
    result = await response.json()
    assert response.status == status, result
    return result


async def get(client, actor, operation, status=200, **params):
    response = await client.get(f"/miniapp/api/{operation}", params=params, headers=headers(actor))
    result = await response.json()
    assert response.status == status, result
    return result


async def test_repertoire_mutations_and_decisions_share_the_bot_queue(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    band = str(world.group.id)
    async with (
        TestClient(TestServer(create_app(settings(engine), engine))) as client,
        Bot("123456:TEST", session=FakeTelegram()) as bot,
    ):
        member = await post(client, 2, "open_repertoire", {"band": band})
        admin = await post(client, 1, "open_repertoire", {"band": band})
        await post(client, 2, "open_repertoire", {"band": str(world.other.id)}, 403)
        await get(client, 2, "repertoire_proposals", 403, band=band)
        request = {**member, **payload(world, create=True)}
        submitted = await post(client, 2, "catalog_save", request)
        assert await post(client, 2, "catalog_save", request) == submitted
        await post(client, 1, "catalog_save", request, 401)
        assert await deliver_one_outbox(bot, engine)
        receipt = await get(client, 2, "catalog_status", **member, request=submitted["request"])
        assert receipt["status"] == "saved" and receipt["proposed"]
        assert len((await get(client, 2, "repertoire", band=band))["songs"]) == 1
        reviews = (await get(client, 1, "repertoire_proposals", band=band))["proposals"]
        assert len(reviews) == 1 and reviews[0]["fields"]["aliases"] == request["fields"]["aliases"]
        proposal_id = reviews[0]["id"]
        async with PostgresUnitOfWork(engine) as uow:
            controller = Controller(
                uow.store,
                world.admin,
                -100,
                private=False,
                bot_username="example_bot",
                miniapp_public_base_url="https://example.org",
            )
            proposals = await controller.execute("/proposals")
            yes, no, shortcut = [action for _, action in proposals.buttons]
            assert (yes, no) == (f"decide yes {proposal_id}", f"decide no {proposal_id}")
            assert shortcut.endswith(f"ws_songs_{world.group.id.hex}_{UUID(proposal_id).hex}")
        focus = await get(
            client, 1, "repertoire_proposals", band=band, page=1, proposal=proposal_id
        )
        assert focus["page"] == 0 and focus["proposals"] == reviews
        decision = {"proposal_id": reviews[0]["id"], "approve": True, "request_id": str(uuid4())}
        await post(client, 2, "catalog_decide", {**member, **decision}, 403)
        await post(client, 1, "catalog_decide", {**admin, **decision})
        assert await deliver_one_outbox(bot, engine)
        assert not (await get(client, 1, "repertoire_proposals", band=band))["proposals"]
        songs = (await get(client, 2, "repertoire", band=band))["songs"]
        assert {song["title"] for song in songs} == {"Комета", request["fields"]["title"]}

        edit = {**member, **payload(world)}
        edit["fields"]["aliases"] = []
        await post(client, 2, "catalog_save", edit)
        assert await deliver_one_outbox(bot, engine)
        reviews = (await get(client, 1, "repertoire_proposals", band=band))["proposals"]
        assert reviews[0]["original_title"] == "Комета"
        reject = {"proposal_id": reviews[0]["id"], "approve": False, "request_id": str(uuid4())}
        await post(client, 1, "catalog_decide", {**admin, **reject})
        assert await deliver_one_outbox(bot, engine)
        original = (await get(client, 2, "repertoire", band=band, song=str(world.song.id)))["song"]
        assert original["title"] == "Комета"

        action = {
            "song_id": original["id"],
            "expected": original["version"],
            "request_id": str(uuid4()),
        }
        await post(client, 2, "catalog_archive", {**member, **action}, 403)
        await post(client, 1, "catalog_archive", {**admin, **action})
        assert await deliver_one_outbox(bot, engine)
        archived = (await get(client, 2, "repertoire", band=band, archived="1"))["songs"]
        assert [song["id"] for song in archived] == [original["id"]]
        restore = {**action, "request_id": str(uuid4()), "expected": original["version"] + 1}
        await post(client, 2, "catalog_restore", {**member, **restore}, 403)
        await post(client, 1, "catalog_restore", {**admin, **restore})
        assert await deliver_one_outbox(bot, engine)
        assert not (await get(client, 2, "repertoire", band=band, archived="1"))["songs"]
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(PersonalDraft)
            events = await uow.store.find(Outbox, support_kind="catalog")
            assert events and all(event.concert_id is None and event.sent for event in events)


@pytest.mark.parametrize("change", ["epoch", "membership"])
async def test_worker_rechecks_standalone_repertoire_scope(engine, world, change):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    band = str(world.group.id)
    async with (
        TestClient(TestServer(create_app(settings(engine), engine))) as client,
        Bot("123456:TEST", session=FakeTelegram()) as bot,
    ):
        member = await post(client, 2, "open_repertoire", {"band": band})
        await post(client, 2, "catalog_save", {**member, **payload(world, create=True)})
        async with PostgresUnitOfWork(engine) as uow:
            if change == "epoch":
                group = await uow.store.get(Group, world.group.id)
                group.lifecycle_version += 1
            else:
                rows = await uow.store.find(
                    Membership, group_id=world.group.id, user_id=world.member.id
                )
                rows[0].status = "left"
        assert not await deliver_one_outbox(bot, engine)
        assert len((await get(client, 1, "repertoire", band=band))["songs"]) == 1
        assert not (await get(client, 1, "repertoire_proposals", band=band))["proposals"]
