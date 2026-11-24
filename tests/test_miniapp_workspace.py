from datetime import date

from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings, signed

from setlist_bot.application import workspace
from setlist_bot.application.catalog import Catalog
from setlist_bot.domain.entities import Confirmation, DeploymentIdentity, Membership, PersonalDraft
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def test_workspace_reuses_membership_and_performance_permissions(engine, world, monkeypatch):
    from conftest import invite_fixture_member

    monkeypatch.setattr(workspace, "today", lambda: date(2026, 12, 31))
    monkeypatch.setattr(workspace, "PAGE_SIZE", 1)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        uow.store.add(
            Membership(group_id=world.other.id, user_id=world.member.id, status="invited")
        )
        await invite_fixture_member(uow.store, world.concert2, world.member)

    admin = {"Authorization": "tma " + signed()}
    member = {"Authorization": "tma " + signed(actor=2)}
    band = str(world.group.id)
    performances = f"/miniapp/api/performances?band={band}"
    payload = {"band": band, "performance": str(world.concert2.id)}

    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        assert (await client.get("/miniapp/api/bands")).status == 401
        bands = await (await client.get("/miniapp/api/bands", headers=member)).json()
        assert bands["bands"] == [{"id": band, "name": world.group.name}]
        first = await (await client.get(performances, headers=admin)).json()
        assert first["has_next"] and first["performances"][0]["id"] == str(world.concert.id)
        second = await (await client.get(performances + "&page=1", headers=admin)).json()
        assert not second["has_next"] and second["performances"][0]["id"] == payload["performance"]
        invited = await (await client.get(performances, headers=member)).json()
        assert (
            not invited["has_next"] and invited["performances"][0]["id"] == payload["performance"]
        )
        foreign = f"/miniapp/api/performances?band={world.other.id}"
        assert (await client.get(foreign, headers=member)).status == 403

        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(Confirmation)
            assert not await uow.store.find(PersonalDraft)

        url = "/miniapp/api/open_draft"
        assert (await client.post(url, json=payload, headers=member)).status == 401
        origin = {"Origin": "https://example.org"}
        hidden = {**payload, "performance": str(world.concert.id)}
        assert (await client.post(url, json=hidden, headers=member | origin)).status == 403
        cross_band = {**payload, "performance": str(world.other_concert.id)}
        assert (await client.post(url, json=cross_band, headers=admin | origin)).status == 403
        opened = await client.post(url, json=payload, headers=member | origin)
        assert opened.status == 200
        state_url = "/miniapp/api/state?session=" + (await opened.json())["session"]
        assert (await client.get(state_url, headers=admin)).status == 401
        state = await (await client.get(state_url, headers=member)).json()
        assert state["concert_id"] == payload["performance"] and state["entries"] == []

        async with PostgresUnitOfWork(engine) as uow:
            membership = (
                await uow.store.find(Membership, user_id=world.member.id, group_id=world.group.id)
            )[0]
            membership.status = "left"
        assert (await client.get(state_url, headers=member)).status == 403


async def test_repertoire_without_event_access_and_with_isolated_song_ids(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        await Catalog(uow.store, world.admin).alias(world.group.id, world.song.id, "Вечеринки")
    headers = {"Authorization": "tma " + signed(actor=2)}
    endpoint = "/miniapp/api/repertoire"
    params = {"band": str(world.group.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        response = await client.get(
            endpoint, params=params | {"query": "Vecherinki"}, headers=headers
        )
        assert response.status == 200
        songs = (await response.json())["songs"]
        assert songs == [{"id": str(world.song.id), "title": "Комета", "duration": "03:38"}]
        card = await client.get(endpoint, params=params | {"song": songs[0]["id"]}, headers=headers)
        song = (await card.json())["song"]
        assert song["aliases"] == ["Вечеринки"] and song["bpm"] == 110 and not song["archived"]
        cross_band = params | {"song": str(world.other_song.id)}
        assert (await client.get(endpoint, params=cross_band, headers=headers)).status == 403
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(PersonalDraft)
            catalog = Catalog(uow.store, world.admin)
            await catalog.archive(world.group.id, world.song.id, song["version"], True)
        active = await client.get(endpoint, params=params, headers=headers)
        assert (await active.json())["songs"] == []
        archive = await client.get(endpoint, params=params | {"archived": "1"}, headers=headers)
        assert (await archive.json())["songs"] == songs
