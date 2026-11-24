from uuid import uuid4

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_telegram import FakeTelegram

from setlist_bot.application.catalog import Catalog
from setlist_bot.domain.entities import DeploymentIdentity, Song
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app


async def test_table_preview_mapping_and_commit_without_concert(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    band = str(world.group.id)
    source = {
        "source": "Length\tSong Name\tEffects\tKEY\n03:43\tРозовый фламинго\tлазер\tBbmin",
        "has_header": True,
    }
    async with (
        TestClient(TestServer(create_app(settings(engine), engine))) as client,
        Bot("123456:TEST", session=FakeTelegram()) as bot,
    ):
        url = "repertoire_import_preview"
        await post(client, 2, url, {**source, "band": band}, 403)
        unmapped = await post(client, 1, url, {**source, "band": band})
        assert not unmapped["ready"] and unmapped["columns"] == [
            "Length",
            "Song Name",
            "Effects",
            "KEY",
        ]
        source["mapping"] = {"title": 1, "time": 0, "lights": 2, "bpm": -1, "comment": -1}
        mapped = await post(client, 1, url, {**source, "band": band})
        assert mapped["ready"] and mapped["new"][0]["comment"] == "KEY: Bbmin"
        async with PostgresUnitOfWork(engine) as uow:
            assert len(await uow.store.find(Song, group_id=world.group.id)) == 1
        admin = await post(client, 1, "open_repertoire", {"band": band})
        request = {
            **admin,
            **source,
            "request_id": str(uuid4()),
            "fingerprint": mapped["fingerprint"],
        }
        queued = await post(client, 1, "catalog_import", request)
        assert await deliver_one_outbox(bot, engine)
        result = await get(client, 1, "catalog_status", **admin, request=queued["request"])
        assert result["added"] == 1 and result["supplemented"] == 0
        assert await post(client, 1, "catalog_import", request) == result
        assert not await deliver_one_outbox(bot, engine)
        async with PostgresUnitOfWork(engine) as uow:
            imported = await Catalog(uow.store, world.admin).exact_song(
                world.group.id, "Розовый фламинго"
            )
            assert imported and imported.lights == "лазер" and imported.comment == "KEY: Bbmin"


async def test_import_rejects_stale_preview_in_worker(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        song = await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Интро", "duration_seconds": 5}
        )
    band = str(world.group.id)
    source = {"source": "TRACKS,TIME,BPM\nИнтро,00:05,99\nДругая песня,03:10,100"}
    async with (
        TestClient(TestServer(create_app(settings(engine), engine))) as client,
        Bot("123456:TEST", session=FakeTelegram()) as bot,
    ):
        preview = await post(client, 1, "repertoire_import_preview", {**source, "band": band})
        assert preview["ready"] and preview["supplements"] == [
            {"title": "Интро", "fields": {"bpm": 99}}
        ]
        admin = await post(client, 1, "open_repertoire", {"band": band})
        request = {
            **admin,
            **source,
            "request_id": str(uuid4()),
            "fingerprint": preview["fingerprint"],
        }
        wrong_plan = await post(
            client, 1, "catalog_import", {**request, "fingerprint": "stale"}, 409
        )
        assert wrong_plan["error"] == "import_changed"
        queued = await post(client, 1, "catalog_import", request)
        async with PostgresUnitOfWork(engine) as uow:
            await Catalog(uow.store, world.admin).save(
                world.group.id,
                {"title": "Интро", "duration_seconds": 5, "bpm": 112},
                song.id,
                song.version,
            )
        assert not await deliver_one_outbox(bot, engine)
        failed = await get(client, 1, "catalog_status", **admin, request=queued["request"])
        assert failed["status"] == "failed" and failed["error"] == "import_changed"
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.get(Song, song.id)).bpm == 112
            assert not await Catalog(uow.store, world.admin).exact_song(
                world.group.id, "Другая песня"
            )
