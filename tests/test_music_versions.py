"""RU/EN: Import defaults are reversible; cleaning must never silently merge versions."""

from copy import deepcopy
from datetime import timedelta

from test_music_search import ready
from test_setlist_menus import sender

from setlist_bot.application.music_search import MusicSearches
from setlist_bot.domain.entities import MusicCache, MusicSearch, Song, SongAlias, now
from setlist_bot.domain.music_discovery import (
    catalog_titles,
    default_exclusions,
    merged_tracks,
    without_featured,
)
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.music_worker import search_one


def track(title, seconds=180):
    return {"title": title, "seconds": seconds, "bpm": None, "bpms": []}


def test_credits_clean_only_catalog_name_and_other_qualifiers_stay():
    rows = [
        track("Комета (feat. Друг)"),
        track("Снег (Live) (feat Друг)"),
        track("Ветер (DJ Remix) [feat. Друг]"),
    ]
    before = deepcopy(rows)
    assert catalog_titles(rows, []) == ["Комета", "Снег", "Ветер (DJ Remix)"]
    assert rows == before
    assert without_featured("Feat of courage (Remix)") == "Feat of courage (Remix)"
    # An artist named Live or Remix in a credit is not a recording-version marker.
    assert default_exclusions([track("Свет (feat. Remix)"), track("Ночь (feat. Live)")]) == []


def test_defaults_prefer_live_exclude_remix_but_keep_unpaired_regular_song():
    rows = [
        track("Снег (feat. Друг)"),
        track("Снег (LIVE)"),
        track("Снег (DJ Remix)"),
        track("Ночь"),
        track("Live forever"),
        track("Ночь [Remix]"),
    ]
    assert default_exclusions(rows) == [0, 2, 5]


def test_live_name_stays_on_conflict_with_selection_catalog_or_alias():
    assert catalog_titles([track("Снег"), track("снег (Live)")], []) == ["Снег", "снег (Live)"]
    assert catalog_titles([track("Снег (Live) (feat. Гость)")], ["СНЕГ"]) == ["Снег (Live)"]
    assert catalog_titles([track("Снег (Live at A)"), track("Снег (Live at B)")], []) == [
        "Снег (Live at A)",
        "Снег (Live at B)",
    ]
    assert catalog_titles([track("Снег (Live)")], ["Другая песня"]) == ["Снег"]


def test_merge_keeps_available_feature_credit_for_first_selection():
    rows = merged_tracks(
        [
            {**track("Ночь"), "source": "Apple Music"},
            {**track("Ночь (feat. Гость)"), "source": "SongBPM"},
        ]
    )
    assert len(rows) == 1 and rows[0]["title"] == "Ночь (feat. Гость)"


async def test_fresh_and_cached_searches_get_same_defaults_without_resetting_clicks(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = MusicSearches(uow.store, world.admin, 1)
        job = await service.create(world.group.id, "Artist")
        identifier = job.id
        job.data = {**job.data, "phase": "songs", "artist": {"id": "versions", "name": "Artist"}}
        job.next_attempt_at = now() - timedelta(seconds=1)

    class Source:
        async def step(self, data, query):
            data["raw"] = [track("Снег"), track("Снег (Live)"), track("Снег (Remix)")]
            data["phase"] = "finish"
            return data, "choosing"

    await search_one(engine, Source())
    async with PostgresUnitOfWork(engine) as uow:
        service = MusicSearches(uow.store, world.admin, 1)
        job = await service.load(identifier)
        expected = default_exclusions(job.data["tracks"])
        assert job.data["excluded"] == expected and len(expected) == 2
        service.toggle(job, expected[0])
        assert job.data["excluded"] == expected[1:]
        job.status = "cancelled"
        cache = (await uow.store.find(MusicCache))[0]
        assert "excluded" not in cache.payload
        cached = await service.create(world.group.id, "Artist")
        cached.status = "artists"
        cached.data = {**cached.data, "artists": [{"id": "versions", "name": "Artist"}]}
        await service.choose_artist(cached, 0)
        assert cached.status == "choosing" and cached.data["excluded"] == expected


async def test_table_shows_every_duration_and_full_credit_but_preview_and_save_are_clean(
    engine, world
):
    identifier = await ready(engine, world, 1)
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        job.data = {
            **job.data,
            "tracks": [
                track("Комета (feat. Друг)", 218),
                track("Новая (Live) (feat. Друг)", 241),
                track("Ремикс (Remix)", 205),
                track("Без времени", None),
            ],
            "excluded": [2, 3],
        }
    send = sender(engine)
    selection = await send(f"mi_view {identifier.hex}", private=True)
    cells = selection.articles[0].blocks[1]["cells"]
    assert cells[1][0]["text"] == "Комета (feat. Друг) · 03:38"
    assert cells[2][0]["text"] == "Новая (Live) (feat. Друг) · 04:01"
    assert cells[3][0]["text"] == {"type": "strikethrough", "text": "Ремикс (Remix) · 03:25"}
    assert "нужно указать время" in cells[4][0]["text"]["text"]
    preview = await send(f"mi_preview {identifier.hex}", private=True)
    assert "Уже есть в каталоге: 1" in preview.text
    assert preview.articles[0].blocks[1]["cells"][1][0]["text"] == "Новая"
    commit = next(action for _, action in preview.buttons if action.startswith("mi_commit"))
    await send(commit, private=True)
    await send(commit, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        songs = await uow.store.find(Song, group_id=world.group.id)
        assert {song.title for song in songs} == {"Комета", "Новая"}
        job = await uow.store.get(MusicSearch, identifier)
        assert job.data["tracks"][1]["title"] == "Новая (Live) (feat. Друг)"


async def test_live_conflict_with_existing_alias_keeps_version_in_real_import(engine, world):
    identifier = await ready(engine, world, 1)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(SongAlias(group_id=world.group.id, song_id=world.song.id, title="Хвост"))
        job = await uow.store.get(MusicSearch, identifier)
        job.data = {**job.data, "tracks": [track("Хвост (Live) (feat. Гость)")]}
    response = await sender(engine)(f"mi_preview {identifier.hex}", private=True)
    assert response.articles[0].blocks[1]["cells"][1][0]["text"] == "Хвост (Live)"
