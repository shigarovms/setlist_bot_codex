from copy import deepcopy

import pytest

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.exports import Exports
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import ExportFile
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork


class Renderer:
    def __init__(self):
        self.payloads = []

    def render(self, payload, audience, language, transliterate):
        self.payloads.append(deepcopy(payload))
        return b"%PDF-" + audience.encode()


async def approved_export(uow, world):
    lists = Setlists(uow.store, world.admin)
    await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета", "2 мин", "Комета"])
    await lists.approve(world.group.id, world.concert.id, 1)
    snapshot, version, approved = await lists.read(world.group.id, world.concert.id)
    exports = Exports(uow.store, world.admin, -100)
    return exports, await exports.create(
        world.group.id,
        world.concert.id,
        snapshot,
        version,
        approved=approved,
        private=False,
        back="setlist",
    )


async def test_lighting_refresh_keeps_approved_timing_and_cached_files(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        exports, source = await approved_export(uow, world)
        renderer = Renderer()
        await exports.files(source.id, "all", "en", False, renderer)
        cached = await uow.store.find(ExportFile, export_id=source.id)
        original = deepcopy(source.payload)
        await Catalog(uow.store, world.admin).save(
            world.group.id,
            {
                "title": "Новое название",
                "duration_seconds": 500,
                "bpm": 130,
                "lights": "синий",
                "comment": "Новый комментарий",
            },
            world.song.id,
            1,
        )
        renderer = Renderer()
        await exports.files(source.id, "all", "en", False, renderer)
        musicians, lighting = renderer.payloads
        for payload in (musicians, lighting):
            items = payload["snapshot"]["blocks"][0]["items"]
            assert items[0]["title"] == "Комета" and items[0]["duration_seconds"] == 218
            assert items[1]["kind"] == "pause" and items[1]["duration_seconds"] == 120
            assert items[2]["song_id"] == items[0]["song_id"]
        assert lighting["snapshot"]["blocks"][0]["items"][0]["bpm"] == 130
        assert lighting["snapshot"]["blocks"][0]["items"][0]["lights"] == "синий"
        assert musicians["snapshot"]["blocks"][0]["items"][0]["bpm"] == 110
        assert source.payload == original
        assert await uow.store.find(ExportFile, export_id=source.id) == cached


async def test_export_rejects_draft_and_foreign_chat_owner_and_membership(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        exports, source = await approved_export(uow, world)
        for service in (
            Exports(uow.store, world.stranger, -100),
            Exports(uow.store, world.member, -200),
        ):
            with pytest.raises(AppError):
                await service.load(source.id)
        with pytest.raises(AppError, match="export_approved_only"):
            await exports.create(
                world.group.id,
                world.concert.id,
                source.payload["snapshot"],
                2,
                approved=False,
                private=True,
                back="setlist",
            )


async def test_repeated_export_reuses_existing_bytes(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        exports, source = await approved_export(uow, world)
        renderer = Renderer()
        first = await exports.files(source.id, "lights", "ru", False, renderer)
        second = await exports.files(source.id, "lights", "ru", False, renderer)
        assert first == second and len(renderer.payloads) == 1
