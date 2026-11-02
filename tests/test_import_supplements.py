"""RU: Импорт дополняет пустое после согласия. EN: Preserve existing data and approval rules."""

import pytest
from test_article_import import article
from test_setlist_menus import sender

from setlist_bot.application.catalog import SONG_FIELDS, Catalog
from setlist_bot.application.importing import Importer
from setlist_bot.domain.entities import CatalogImport, CatalogProposal, Song
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def empty_fields(engine, world, *, archived=False):
    async with PostgresUnitOfWork(engine) as uow:
        catalog = Catalog(uow.store, world.admin)
        song = await catalog.song(world.group.id, world.song.id)
        data = {key: getattr(song, key) for key in SONG_FIELDS}
        data.update(bpm=None, comment="")
        await catalog.save(world.group.id, data, song.id, song.version)
        await catalog.alias(world.group.id, song.id, "Падающая")
        song.archived = archived
        return song.version


@pytest.mark.parametrize("private", [False, True])
async def test_mixed_import_previews_and_supplements_existing_record_without_overwriting(
    engine, world, private
):
    version = await empty_fields(engine, world, archived=True)
    send = sender(engine)
    if private:
        await send(f"/use_group {world.group.id}", private=True)
    preview = await send(
        "/add_new_songs\nПадающая\t09:00\tдругие цвета\t125\tВыход актёра\nНовая\t03:20",
        private=private,
    )
    assert "новых — 1" in preview.text and "Песен для дополнения: 1" in preview.text
    assert "Дополнить пустые поля «Комета»:\nBPM: 125\nКомментарий: Выход актёра" in preview.text
    assert "другие цвета" not in preview.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Song, world.song.id)).bpm is None
    saved = await send(preview.buttons[0][1], private=private)
    assert "Добавлено песен: 1. Дополнено песен: 1" in saved.text
    await send(preview.buttons[0][1], private=private)
    async with PostgresUnitOfWork(engine) as uow:
        song = await uow.store.get(Song, world.song.id)
        assert song.version == version + 1
        assert song.title == "Комета" and song.duration_seconds == 218 and song.archived
        assert song.lights == "белый, красный" and song.bpm == 125
        assert song.comment == "Выход актёра"
        assert len(await uow.store.find(Song, group_id=world.group.id)) == 2
        assert (await uow.store.get(Song, world.other_song.id)).bpm is None


@pytest.mark.parametrize("actor", [1, 2])
async def test_single_line_import_supplements_or_proposes_update_without_creating_duplicate(
    engine, world, actor
):
    await empty_fields(engine, world)
    send = sender(engine)
    preview = await send("/add_new_songs Падающая  09:00  красный  125  Подводка", actor=actor)
    assert "Дополнить пустые поля «Комета»" in preview.text
    await send(preview.buttons[0][1], actor=actor)
    async with PostgresUnitOfWork(engine) as uow:
        if actor == 2:
            song = await uow.store.get(Song, world.song.id)
            assert song.bpm is None
            proposal = (await uow.store.find(CatalogProposal, group_id=world.group.id))[0]
            assert proposal.song_id == song.id and proposal.payload["duration_seconds"] == 218
            await Catalog(uow.store, world.admin).decide(world.group.id, proposal.id, True)
        song = await uow.store.get(Song, world.song.id)
        assert song.bpm == 125 and song.comment == "Подводка"
        assert song.duration_seconds == 218 and song.lights == "белый, красный"
        assert len(await uow.store.find(Song, group_id=world.group.id)) == 1


async def test_existing_optional_fields_are_not_overwritten_and_cancel_keeps_missing_fields(
    engine, world
):
    send = sender(engine)
    unchanged = await send("/add_new_songs Комета  09:00  другой свет  150  другой комментарий")
    assert "пустых полей для дополнения нет" in unchanged.text
    assert not any(callback.startswith("confirm ") for _, callback in unchanged.buttons)
    await empty_fields(engine, world)
    preview = await send("/add_new_songs\nКомета\t09:00\t\t125")
    await send("/cancel")
    await send(preview.buttons[0][1])
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Song, world.song.id)).bpm is None
        assert (await uow.store.find(CatalogImport))[0].status == "cancelled"


async def test_catalog_change_after_supplement_preview_invalidates_entire_mixed_batch(
    engine, world
):
    await empty_fields(engine, world)
    send = sender(engine)
    preview = await send("/add_new_songs\nКомета\t09:00\t\t125\nНовая\t00:05")
    async with PostgresUnitOfWork(engine) as uow:
        song = await uow.store.get(Song, world.song.id)
        data = {key: getattr(song, key) for key in SONG_FIELDS}
        data["bpm"] = 180
        await Catalog(uow.store, world.admin).save(world.group.id, data, song.id, song.version)
    rejected = await send(preview.buttons[0][1])
    assert "Каталог или настройки импорта изменились" in rejected.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Song, world.song.id)).bpm == 180
        assert not await uow.store.find(Song, group_id=world.group.id, title="Новая")


@pytest.mark.parametrize("conflicting", [False, True])
async def test_repeated_alias_rows_merge_complementary_fields_but_reject_conflicting_values(
    engine, world, conflicting
):
    await empty_fields(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        importer = Importer(uow.store, world.admin, -100)
        second_bpm = "140" if conflicting else "125"
        job = await importer.create(
            world.group.id,
            f"Комета\t09:00\t\t125\nПадающая\t08:00\t\t{second_bpm}\tВступление",
        )
        plan = await importer.preview(world.group.id, job.id)
        assert len(plan.supplements) == 1
        if conflicting:
            assert plan.errors[0].error == "import_duplicate_conflict"
            with pytest.raises(AppError, match="import_fix"):
                await importer.commit(world.group.id, job.id, plan.fingerprint)
        else:
            result = await importer.commit(world.group.id, job.id, plan.fingerprint)
            assert result.added == 0 and result.supplemented == 1
        song = await uow.store.get(Song, world.song.id)
        assert song.bpm == (None if conflicting else 125)
        assert song.comment == ("" if conflicting else "Вступление")


async def test_internet_import_policy_keeps_existing_songs_unchanged(engine, world):
    await empty_fields(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        importer = Importer(uow.store, world.admin, -100)
        job = await importer.create(world.group.id, "Комета\t09:00\t\t125\nНовая\t00:05")
        plan = await importer.preview(world.group.id, job.id, supplement_existing=False)
        assert not plan.supplements and len(plan.duplicates) == 1
        result = await importer.commit(
            world.group.id, job.id, plan.fingerprint, supplement_existing=False
        )
        assert result.added == 1 and result.supplemented == 0
        assert (await uow.store.get(Song, world.song.id)).bpm is None


async def test_article_import_can_supplement_without_new_songs(engine, world):
    await empty_fields(engine, world)
    processor = Processor(engine)
    preview = await processor.process(
        1000,
        1,
        -100,
        "",
        private=False,
        article=article([["Комета", "09:00", "", "125"]], "/add_new_songs"),
    )
    assert "новых — 0" in preview.text and "Песен для дополнения: 1" in preview.text
    result = await processor.process(
        1001, 1, -100, preview.buttons[0][1], private=False, is_callback=True
    )
    assert "Добавлено песен: 0. Дополнено песен: 1" in result.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Song, world.song.id)).bpm == 125


async def test_ambiguous_existing_titles_are_never_enriched_by_guessing(engine, world):
    await empty_fields(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Комета", "duration_seconds": 300}
        )
        importer = Importer(uow.store, world.admin, -100)
        job = await importer.create(world.group.id, "Комета\t09:00\t\t125")
        plan = await importer.preview(world.group.id, job.id)
        assert plan.errors[0].error == "import_ambiguous" and not plan.supplements
        with pytest.raises(AppError, match="import_fix"):
            await importer.commit(world.group.id, job.id, plan.fingerprint)
        assert all(song.bpm is None for song in await uow.store.find(Song, group_id=world.group.id))


async def test_import_rechecks_admin_permissions_at_confirmation(engine, world):
    from setlist_bot.domain.entities import Membership

    await empty_fields(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        member = (await uow.store.find(Membership, user_id=world.member.id))[0]
        member.role = "admin"
    send = sender(engine)
    preview = await send("/add_new_songs\nКомета\t09:00\t\t125", actor=2)
    async with PostgresUnitOfWork(engine) as uow:
        member = (await uow.store.find(Membership, user_id=world.member.id))[0]
        member.role = "member"
    denied = await send(preview.buttons[0][1], actor=2)
    assert "администратора" in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Song, world.song.id)).bpm is None
