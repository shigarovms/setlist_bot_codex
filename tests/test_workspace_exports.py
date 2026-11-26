from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendDocument
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
        contents = {}
        for target in ("musicians", "lights"):
            pdf = await client.get(
                endpoint,
                params={**session, "request": historical["request"], "audience": target},
                headers=headers(actor),
            )
            assert pdf.status == 200 and pdf.content_type == "application/pdf"
            assert f"-{target}-en-latin.pdf" in pdf.headers["Content-Disposition"]
            contents[target] = pdf_text(await pdf.read())
        assert "Kometa" in contents["musicians"]
        assert "147" in contents["lights"] and "03:38" in contents["lights"]
        denied_file = await client.get(
            endpoint, params={**params, "audience": "lights"}, headers=headers(actor)
        )
        assert denied_file.status == 403
        still_published = await client.get(endpoint, params=params, headers=headers(actor))
        assert await still_published.read() == original
        async with PostgresUnitOfWork(engine) as uow:
            member = (await uow.store.find(Membership, group_id=group_id, user_id=world.member.id))[
                0
            ]
            member.status = "left"
        assert (await client.get(endpoint, params=params, headers=headers(actor))).status == 403


async def test_workspace_can_send_one_or_two_pdfs_to_the_authenticated_private_chat(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await lists.approve(world.group.id, world.concert.id, 1)
    bot = SimpleNamespace(send_document=AsyncMock(), send_media_group=AsyncMock())
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 2, "open_shared", scope(world))
        payload = {
            **session,
            "action": "generate",
            "version": 2,
            "language": "ru",
            "delivery": "telegram",
            "chat_id": -100,
        }
        for audience in ("lights", "all"):
            command = {**payload, "request_id": str(uuid4()), "audience": audience}
            queued = await post(client, 2, "export_action", command)
            assert queued["status"] == "pending"
            assert await deliver_request(engine, queued["request"], bot=bot)
            result = await post(client, 2, "export_action", command)
            assert result["status"] == "saved" and result["delivery"] == "telegram"
    bot.send_document.assert_awaited_once()
    chat, upload = bot.send_document.await_args.args
    assert chat == 2 and "-lights-" in upload.filename and upload.data.startswith(b"%PDF-")
    bot.send_media_group.assert_awaited_once()
    chat, media = bot.send_media_group.await_args.args
    assert chat == 2 and len(media) == 2
    assert [document.type for document in media] == ["document", "document"]
    assert "-musicians-" in media[0].media.filename and "-lights-" in media[1].media.filename
    assert all(document.media.data.startswith(b"%PDF-") for document in media)


async def test_preview_and_delivery_use_open_form_without_saving_preset(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"] * 19)
        await lists.approve(world.group.id, world.concert.id, 1)
    chat = SimpleNamespace(send_document=AsyncMock(), send_media_group=AsyncMock())
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        target = scope(world)
        session = await post(client, 2, "open_shared", target)
        stored = (await get(client, 2, "export_options", **target))["settings"]
        appearance = {"font": "serif", "size": 24, "align": "center", "pages": 1}
        selected = {
            "version": 2,
            "language": "ru",
            "audience": "all",
            "settings": appearance,
        }
        preview = await post(client, 2, "export_preview", {**session, **selected})
        assert preview["page_counts"]["musicians"] == 1
        assert preview["font_size"] < 24 and preview["recommended_pages"] > 1
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(ExportSnapshot)
            assert not await uow.store.find(ExportFile)
        download = await execute(client, engine, 2, session, {**selected, "action": "generate"})
        for audience, pages in preview["page_counts"].items():
            file = await client.get(
                "/miniapp/api/export_download",
                params={**session, "request": download["request"], "audience": audience},
                headers=headers(2),
            )
            assert file.status == 200
            assert len(PdfReader(BytesIO(await file.read())).pages) == pages
        send = {
            **session,
            **selected,
            "action": "generate",
            "delivery": "telegram",
            "request_id": str(uuid4()),
            "audience": "musicians",
        }
        queued = await post(client, 2, "export_action", send)
        assert await deliver_request(engine, queued["request"], bot=chat)
        chat.send_document.assert_awaited_once()
        _, upload = chat.send_document.await_args.args
        pdf = PdfReader(BytesIO(upload.data))
        sizes = []
        pdf.pages[0].extract_text(visitor_text=lambda _v, _cm, _tm, _f, size: sizes.append(size))
        assert max(sizes) == preview["font_size"]
        assert (await get(client, 2, "export_options", **target))["settings"] == stored
        await execute(client, engine, 2, session, {"action": "settings", "settings": appearance})
        assert (await get(client, 2, "export_options", **target))["settings"] == appearance
        await post(client, 1, "export_preview", {**session, **selected}, 401)
        await post(client, 2, "export_preview", {**session, **selected, "version": 1}, 409)


async def test_workspace_reports_unavailable_chat_and_can_retry_with_a_download(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await lists.approve(world.group.id, world.concert.id, 1)
    blocked = TelegramForbiddenError(
        method=SendDocument(chat_id=2, document="unused"), message="Test blocked chat"
    )
    bot = SimpleNamespace(
        send_document=AsyncMock(side_effect=blocked), send_media_group=AsyncMock()
    )
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 2, "open_shared", scope(world))
        payload = {"action": "generate", "version": 2, "language": "ru", "audience": "musicians"}
        queued = await post(
            client,
            2,
            "export_action",
            {
                **session,
                **payload,
                "request_id": str(uuid4()),
                "delivery": "telegram",
            },
        )
        assert not await deliver_request(engine, queued["request"], bot=bot)
        result = await get(client, 2, "catalog_status", **session, request=queued["request"])
        assert result["status"] == "failed" and result["error"] == "export_chat_unavailable"
        saved = await execute(client, engine, 2, session, {**payload, "delivery": "download"})
        assert saved["status"] == "saved"
        pdf = await client.get(
            "/miniapp/api/export_download",
            params={**session, "request": saved["request"]},
            headers=headers(2),
        )
        assert pdf.status == 200 and pdf.content_type == "application/pdf"
