from datetime import timedelta
from uuid import uuid4

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post

from setlist_bot.application.catalog import Catalog
from setlist_bot.domain.entities import DeploymentIdentity, Membership, MusicSearch, Outbox, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.miniapp_catalog_delivery import deliver_catalog
from setlist_bot.infrastructure.music_worker import search_one
from setlist_bot.miniapp.app import create_app
from setlist_bot.miniapp.browser_login import SESSION_COOKIE, start_session


class TestSources:
    __test__ = False

    async def step(self, data, query):
        assert query == "Example artist"
        if data["phase"] == "resolve":
            data["artists"] = [{"id": "test-artist", "name": query, "examples": ["Новая"]}]
            return data, "artists"
        data["phase"] = "finish"
        data["raw"] = [
            {"title": "Новая", "seconds": None, "bpm": None, "source": "Example source"},
            {"title": "Комета", "seconds": 218, "bpm": 125, "source": "Example source"},
        ]
        return data, "choosing"


async def deliver_request(engine, request_id):
    async with PostgresUnitOfWork(engine) as uow:
        events = await uow.store.find(Outbox, support_kind="catalog", sent=False)
        event = next(row for row in events if row.payload["request_id"] == request_id)
        return await deliver_catalog(uow, event)


async def ready_worker(engine, group_id):
    async with PostgresUnitOfWork(engine) as uow:
        jobs = await uow.store.find(MusicSearch, group_id=group_id, status="searching")
        jobs[0].next_attempt_at = now() - timedelta(seconds=1)
    assert await search_one(engine, TestSources())


async def test_workspace_search_completes_same_job_in_browser_and_miniapp(engine, world):
    config = settings(engine).model_copy(update={"browser_login_secret": SecretStr("test-only")})
    browser = web.Response()
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        start_session(uow.store, world.admin, browser)
    band = str(world.group.id)
    cookie = {"Cookie": f"{SESSION_COOKIE}={browser.cookies[SESSION_COOKIE].value}"}
    async with TestClient(TestServer(create_app(config, engine))) as client:
        await get(client, 2, "music_search", 403, band=band)
        session = await post(client, 1, "open_repertoire", {"band": band})

        async def action(name, job=None, *, ok=True, **parameters):
            version = {"job": job["id"], "version": job["version"]} if job else {}
            body = {**session, **version, **parameters, "action": name, "request_id": str(uuid4())}
            receipt = await post(client, 1, "music_action", body)
            assert receipt["status"] == "pending"
            assert await deliver_request(engine, receipt["request"]) is ok
            return await get(client, 1, "catalog_status", **session, request=receipt["request"])

        await post(
            client,
            1,
            "music_action",
            {
                **session,
                "action": "start",
                "query": "https://example.test/artist/1",
                "request_id": str(uuid4()),
            },
            403,
        )
        receipt = await action("start", query="Example artist")
        await ready_worker(engine, world.group.id)
        found = await get(client, 1, "music_search", band=band)
        browser_state = await client.get(
            "/miniapp/api/music_search", params={"band": band}, headers=cookie
        )
        assert await browser_state.json() == found
        artist = found["search"]
        assert artist["status"] == "artists" and artist["id"] == receipt["job"]
        assert artist["artists"] == [{"name": "Example artist", "examples": ["Новая"]}]
        await action("artist", artist, index=0)
        await ready_worker(engine, world.group.id)

        selected = (await get(client, 1, "music_search", band=band))["search"]
        assert selected["status"] == "choosing" and len(selected["tracks"]) == 2
        index = next(song["index"] for song in selected["tracks"] if song["title"] == "Новая")
        await action("all", selected, include=False)
        stale = await action("toggle", selected, index=index, ok=False)
        assert stale["error"] == "music_conflict"
        latest = (await get(client, 1, "music_search", band=band))["search"]
        assert not any(song["selected"] for song in latest["tracks"])
        await action("all", latest, include=True)
        latest = (await get(client, 1, "music_search", band=band))["search"]
        assert (await action("preview", latest, ok=False))["error"] == "music_missing_duration"
        await action("duration", latest, index=index, value="03:30")
        latest = (await get(client, 1, "music_search", band=band))["search"]
        await action("preview", latest)
        preview = (await get(client, 1, "music_search", band=band))["search"]
        assert preview["status"] == "preview" and preview["duplicates"] == 1
        assert preview["songs"][0]["title"] == "Новая"
        async with PostgresUnitOfWork(engine) as uow:
            assert not await Catalog(uow.store, world.admin).exact_song(world.group.id, "Новая")
        await action("commit", preview)
        result = (await get(client, 1, "music_search", band=band))["search"]
        assert result["status"] == "done" and result["imported"] == 1
        async with PostgresUnitOfWork(engine) as uow:
            catalog = Catalog(uow.store, world.admin)
            song = await catalog.exact_song(world.group.id, "Новая")
            assert song and song.duration_seconds == 210
            assert (await catalog.song(world.group.id, world.song.id)).bpm == 110


async def test_workspace_music_hides_other_admin_jobs_and_rechecks_permission(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        members = await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        members[0].role = "admin"
    band = str(world.group.id)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": band})
        request = {"action": "start", "query": "Example artist", "request_id": str(uuid4())}
        receipt = await post(client, 1, "music_action", {**admin, **request})
        assert await deliver_request(engine, receipt["request"])
        owner = (await get(client, 1, "music_search", band=band))["search"]
        assert (await get(client, 2, "music_search", band=band))["search"] is None
        member = await post(client, 2, "open_repertoire", {"band": band})
        cancel = {
            "action": "cancel",
            "job": owner["id"],
            "version": owner["version"],
            "request_id": str(uuid4()),
        }
        queued = await post(client, 2, "music_action", {**member, **cancel})
        assert not await deliver_request(engine, queued["request"])
        assert (await get(client, 2, "catalog_status", **member, request=queued["request"]))[
            "error"
        ] == "forbidden"
        queued = await post(client, 1, "music_action", {**admin, **cancel})
        async with PostgresUnitOfWork(engine) as uow:
            memberships = await uow.store.find(
                Membership, group_id=world.group.id, user_id=world.admin.id
            )
            memberships[0].role = "member"
        assert not await deliver_request(engine, queued["request"])
        await get(client, 1, "music_search", 403, band=band)
