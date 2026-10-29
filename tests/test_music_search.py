import asyncio
from datetime import timedelta

from test_setlist_menus import sender

from setlist_bot.application.music_search import MusicSearches
from setlist_bot.domain.entities import Group, Membership, MusicSearch, Outbox, Song, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def ready(engine, world, count=43):
    async with PostgresUnitOfWork(engine) as uow:
        service = MusicSearches(uow.store, world.admin, 1)
        job = await service.create(world.group.id, "Artist")
        job.status = "choosing"
        job.data = {
            "artist": {"name": "Artist", "id": "123"},
            "tracks": [
                {
                    "title": f"Песня {index:02}",
                    "seconds": 180 + index,
                    "bpm": 120 if index % 2 else None,
                    "bpms": [120] if index % 2 else [],
                    "sources": [],
                }
                for index in range(count)
            ],
            "excluded": [],
            "panel_ids": [700],
        }
        identifier = job.id
    return identifier


async def test_internet_entry_uses_existing_admin_rights_and_private_force_reply(engine, world):
    send = sender(engine)
    member = await send("/catalog", actor=2)
    assert not any(action.startswith("mi_open") for _, action in member.buttons)
    admin = await send("/catalog")
    assert any(action.startswith("mi_open") for _, action in admin.buttons)
    group_response = await send(f"mi_open {world.group.id.hex}")
    assert world.group.name in group_response.text and not group_response.force_reply
    ask = await send(f"mi_open {world.group.id.hex}", private=True)
    manual = next(a for _, a in ask.buttons if a.startswith("mi_name_manual"))
    prompt = await send(manual, private=True, remember=500)
    assert prompt.force_reply
    queued = await send("Artist", private=True, reply=500)
    assert "20–30" in queued.text
    denied = await send(f"mi_open {world.group.id.hex}", actor=2, private=True)
    assert not denied.force_reply and not denied.buttons


async def test_exclusions_persist_across_pages_and_fast_clicks_coalesce(engine, world):
    identifier = await ready(engine, world)
    send = sender(engine)
    first = await send(f"mi_view {identifier.hex}", private=True)
    assert len(first.articles[0].blocks[1]["cells"]) == 21
    assert first.edit_message_ids == [700]
    assert (await send(f"mi_toggle {identifier.hex} 0", private=True)).deferred
    await send(f"mi_toggle {identifier.hex} 21", private=True)
    await send(f"mi_page {identifier.hex} 1", private=True)
    second = await send(f"mi_view {identifier.hex}", private=True)
    assert "Страница 2/3" in second.text and "Исключено: 2" in second.text
    assert second.articles[0].blocks[1]["cells"][2][0]["text"]["type"] == "strikethrough"
    async with PostgresUnitOfWork(engine) as uow:
        events = await uow.store.find(Outbox, support_kind="music", sent=False)
        assert len(events) == 1
        assert not await uow.store.find(Song, group_id=world.group.id, title="Песня 01")
    await send(f"mi_toggle {identifier.hex} 0", private=True)
    final = await send(f"mi_view {identifier.hex}", private=True)
    assert "Исключено: 1" in final.text


async def test_two_confirmations_save_only_retained_rows_once_without_replacing_catalog(
    engine, world
):
    identifier = await ready(engine, world, 4)
    send = sender(engine)
    await send(f"mi_toggle {identifier.hex} 1", private=True)
    preview = await send(f"mi_preview {identifier.hex}", private=True)
    assert "3 песен" in preview.text
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Song, group_id=world.group.id)) == 1
    commit = next(action for _, action in preview.buttons if action.startswith("mi_commit"))
    saved = await send(commit, private=True)
    assert "Добавлено 3" in saved.text and "/catalog" in saved.text
    assert [a for _, a in saved.buttons] == ["mi_help manual", "mi_help sheets", "mi_help telegram"]
    again = await send(commit, private=True)
    assert "Добавлено" not in again.text
    async with PostgresUnitOfWork(engine) as uow:
        songs = await uow.store.find(Song, group_id=world.group.id)
        assert {s.title for s in songs} == {"Комета", "Песня 00", "Песня 02", "Песня 03"}
        assert next(s for s in songs if s.title == "Комета").bpm == 110
        assert next(s for s in songs if s.title == "Песня 03").bpm == 120
    stale = await send(f"mi_toggle {identifier.hex} 0", private=True)
    assert stale.deferred is False


async def test_cancel_does_not_change_saved_catalog_or_allow_a_stale_confirmation(engine, world):
    identifier = await ready(engine, world, 2)
    send = sender(engine)
    preview = await send(f"mi_preview {identifier.hex}", private=True)
    commit = next(action for _, action in preview.buttons if action.startswith("mi_commit"))
    cancelled = await send(f"mi_cancel {identifier.hex}", private=True)
    assert "отменены" in cancelled.text
    await send(commit, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Song, group_id=world.group.id)) == 1


async def test_other_users_expiry_lifecycle_and_revoked_admin_cannot_import(engine, world):
    identifier = await ready(engine, world, 2)
    send = sender(engine)
    foreign = await send(f"mi_view {identifier.hex}", actor=2, private=True)
    assert not foreign.articles
    async with PostgresUnitOfWork(engine) as uow:
        group = await uow.store.get(Group, world.group.id)
        group.lifecycle_version += 1
    stale = await send(f"mi_preview {identifier.hex}", private=True)
    assert not stale.articles
    async with PostgresUnitOfWork(engine) as uow:
        group = await uow.store.get(Group, world.group.id)
        group.lifecycle_version -= 1
        memberships = await uow.store.find(
            Membership, group_id=world.group.id, user_id=world.admin.id
        )
        memberships[0].role = "member"
    denied = await send(f"mi_preview {identifier.hex}", private=True)
    assert not denied.articles
    async with PostgresUnitOfWork(engine) as uow:
        memberships = await uow.store.find(
            Membership, group_id=world.group.id, user_id=world.admin.id
        )
        memberships[0].role = "admin"
        job = await uow.store.get(MusicSearch, identifier)
        job.expires_at = now() - timedelta(seconds=1)
    expired = await send(f"mi_preview {identifier.hex}", private=True)
    assert not expired.articles


async def test_back_invalidates_preview_token_and_manual_duration_is_bound_to_original_job(
    engine, world
):
    identifier = await ready(engine, world, 2)
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        tracks = [dict(row) for row in job.data["tracks"]]
        tracks[0]["seconds"] = None
        job.data = {**job.data, "tracks": tracks}
    send = sender(engine)
    prompt = await send(f"mi_preview {identifier.hex}", private=True, remember=999)
    assert prompt.force_reply
    await send(f"/use_group {world.other.id}", private=True)
    preview = await send("03:21", private=True, reply=999)
    assert "2 песен" in preview.text and not preview.force_reply
    old = next(a for _, a in preview.buttons if a.startswith("mi_commit"))
    await send(f"mi_back {identifier.hex}", private=True)
    await send(f"mi_preview {identifier.hex}", private=True)
    rejected = await send(old, private=True)
    assert "Добавлено" not in rejected.text
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.data["durations"]["0"] == 201
        assert job.group_id == world.group.id


async def test_existing_song_is_skipped_and_instructions_have_correct_columns(engine, world):
    identifier = await ready(engine, world, 1)
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        job.data = {
            **job.data,
            "tracks": [{"title": "Комета", "seconds": 999, "bpm": 200, "bpms": [200]}],
        }
    send = sender(engine)
    preview = await send(f"mi_preview {identifier.hex}", private=True)
    assert not any(a.startswith("mi_commit") for _, a in preview.buttons)
    assert "Уже есть в каталоге: 1" in preview.text
    for method in ("manual", "sheets", "telegram"):
        guide = await send("mi_help " + method, private=True)
        assert "/add_new_songs" in guide.text
        if method != "manual":
            assert "BPM" in guide.text and "Комментарий" in guide.text


async def test_simultaneous_final_clicks_are_idempotent(engine, world):
    identifier = await ready(engine, world, 2)
    send = sender(engine)
    preview = await send(f"mi_preview {identifier.hex}", private=True)
    commit = next(a for _, a in preview.buttons if a.startswith("mi_commit"))
    responses = await asyncio.gather(send(commit, private=True), send(commit, private=True))
    assert sum("Добавлено 2" in r.text for r in responses) == 1
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Song, group_id=world.group.id)) == 3


async def test_cancel_duration_prompt_returns_selection_without_cancelling_import(engine, world):
    identifier = await ready(engine, world, 1)
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        job.data = {
            **job.data,
            "tracks": [{"title": "Неизвестная", "seconds": None, "bpm": None, "bpms": []}],
        }
    send = sender(engine)
    prompt = await send(f"mi_preview {identifier.hex}", private=True, remember=111)
    assert prompt.force_reply
    returned = await send("/cancel", private=True)
    assert returned.articles
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(MusicSearch, identifier)).status == "choosing"


async def test_catalog_change_after_preview_rejects_entire_stale_import(engine, world):
    from setlist_bot.application.catalog import Catalog

    identifier = await ready(engine, world, 2)
    send = sender(engine)
    preview = await send(f"mi_preview {identifier.hex}", private=True)
    commit = next(a for _, a in preview.buttons if a.startswith("mi_commit"))
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Песня 00", "duration_seconds": 99}
        )
    rejected = await send(commit, private=True)
    assert "Добавлено" not in rejected.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Song, group_id=world.group.id, title="Песня 01")


async def test_cancelling_an_unrelated_input_does_not_cancel_background_search(engine, world):
    identifier = await ready(engine, world, 2)
    send = sender(engine)
    await send(f"/use_group {world.group.id}", private=True)
    prompt = await send("/concert", private=True)
    assert prompt.force_reply
    await send("/cancel", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(MusicSearch, identifier)).status == "choosing"
