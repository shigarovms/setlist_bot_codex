from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from aiohttp.test_utils import TestClient, TestServer
from pypdf import PdfReader
from test_miniapp import settings
from test_repertoire_workspace_actions import get, headers, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import scope, setup_workspace

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import ExportFile, ExportSnapshot, Membership, User
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


def pdf_text(data):
    assert data.startswith(b"%PDF-")
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)


async def execute(client, engine, actor, session, data, *, ok=True):
    queued = await post(
        client, actor, "export_action", {**session, "request_id": str(uuid4()), **data}
    )
    assert await deliver_request(engine, queued["request"]) is ok
    return await get(client, actor, "catalog_status", **session, request=queued["request"])


async def test_workspace_exports_use_approved_snapshots_and_shared_settings(engine, world):
    await setup_workspace(engine, world)
    group_id, concert_id = world.group.id, world.concert.id
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(group_id, concert_id, 0, ["Комета", "2 мин", "Комета"])
        await lists.approve(group_id, concert_id, 1)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        actor = 2
        session = await post(client, actor, "open_shared", query)
        config = {"font": "serif", "size": 29, "align": "center"}
        await execute(client, engine, actor, session, {"action": "settings", "settings": config})
        assert (await get(client, actor, "export_options", **query))["settings"] == config
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.get(User, world.member.id)).export_settings == config
        generate = {"action": "generate", "version": 2, "language": "ru", "audience": "musicians"}
        first = await execute(client, engine, actor, session, generate)
        params = {**session, "request": first["request"]}
        endpoint = "/miniapp/api/export_download"
        assert (await client.get(endpoint, params=params, headers=headers(1))).status == 401
        other = await post(client, 1, "open_shared", query)
        assert (
            await client.get(endpoint, params={**params, **other}, headers=headers(1))
        ).status == 403
        file = await client.get(endpoint, params=params, headers=headers(actor))
        assert file.status == 200 and file.content_type == "application/pdf"
        assert file.headers["Cache-Control"] == "no-store"
        assert "-v2-musicians-ru.pdf" in file.headers["Content-Disposition"]
        original = await file.read()
        assert "Комета" in pdf_text(original)
        again = await execute(client, engine, actor, session, generate)
        assert again["export"] == first["export"]
        async with PostgresUnitOfWork(engine) as uow:
            snapshots = await uow.store.find(ExportSnapshot, owner_id=world.member.id)
            assert (
                len(snapshots) == 1
                and len(await uow.store.find(ExportFile, export_id=snapshots[0].id)) == 1
            )
            await Setlists(uow.store, world.admin).reopen(group_id, concert_id, 2)
            await Catalog(uow.store, world.admin).save(
                group_id,
                {"title": "Другой заголовок", "duration_seconds": 310, "bpm": 147, "lights": "red"},
                world.song.id,
                1,
            )
        denied = await execute(client, engine, actor, session, {**generate, "version": 3}, ok=False)
        assert denied["error"] == "export_approved_only"
        history = (await get(client, actor, "shared_history", **query))["variants"]
        revision = next(row for row in history if row["action"] == "approved")
        historical = await execute(
            client,
            engine,
            actor,
            session,
            {**generate, "revision": revision["id"], "audience": "all", "language": "en"},
        )
        package = await client.get(
            endpoint, params={**session, "request": historical["request"]}, headers=headers(actor)
        )
        assert package.status == 200 and package.content_type == "application/zip"
        with ZipFile(BytesIO(await package.read())) as bundle:
            assert len(bundle.namelist()) == 2
            lights = next(name for name in bundle.namelist() if "-lights-" in name)
            musicians = next(name for name in bundle.namelist() if "-musicians-" in name)
            assert "Kometa" in pdf_text(bundle.read(musicians))
            assert "147" in pdf_text(bundle.read(lights)) and "03:38" in pdf_text(
                bundle.read(lights)
            )
        still_published = await client.get(endpoint, params=params, headers=headers(actor))
        assert await still_published.read() == original
        async with PostgresUnitOfWork(engine) as uow:
            member = (await uow.store.find(Membership, group_id=group_id, user_id=world.member.id))[
                0
            ]
            member.status = "left"
        assert (await client.get(endpoint, params=params, headers=headers(actor))).status == 403
