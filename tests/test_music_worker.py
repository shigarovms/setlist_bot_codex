import asyncio
from datetime import timedelta

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.music_search import MusicSearches
from setlist_bot.domain.entities import Membership, MusicCache, MusicSearch, Outbox, Song, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.music_sources import SourceError
from setlist_bot.infrastructure.music_worker import search_one


async def queued(engine, world, phase="songs"):
    async with PostgresUnitOfWork(engine) as uow:
        job = await MusicSearches(uow.store, world.admin, 1).create(world.group.id, "Artist")
        job.data = {
            **job.data,
            "phase": phase,
            "artist": {"id": "123", "name": "Artist", "apple_id": 123},
        }
        job.next_attempt_at = now() - timedelta(seconds=1)
        return job.id


class Finish:
    calls = 0

    async def step(self, data, query):
        self.calls += 1
        data["raw"] = [{"title": "Новая", "seconds": 180, "bpm": None, "source": "Apple Music"}]
        data["phase"] = "finish"
        return data, "choosing"


async def test_worker_saves_result_and_notification_without_writing_catalog(engine, world):
    identifier = await queued(engine, world)
    assert await search_one(engine, Finish())
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.status == "choosing" and job.data["tracks"][0]["title"] == "Новая"
        assert "raw" not in job.data
        assert len(await uow.store.find(Song, group_id=world.group.id)) == 1
        assert len(await uow.store.find(Outbox, support_kind="music")) == 1
        assert len(await uow.store.find(MusicCache)) == 1


async def test_step_checkpoint_survives_recreating_worker_and_lease_blocks_duplicate_work(
    engine, world
):
    identifier = await queued(engine, world)

    class First:
        async def step(self, data, query):
            data["phase"], data["checkpoint"] = "albums", 42
            return data, None

    await search_one(engine, First())
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.data["checkpoint"] == 42
        job.next_attempt_at = now() - timedelta(seconds=1)
        job.lease_until = now() + timedelta(seconds=60)
    finish = Finish()
    assert not await search_one(engine, finish) and finish.calls == 0
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        job.lease_until = now() - timedelta(seconds=1)
    assert await search_one(engine, finish)


async def test_slow_source_does_not_hold_group_lock_and_cancel_discards_its_late_result(
    engine, world
):
    identifier = await queued(engine, world)
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(Finish):
        async def step(self, data, query):
            entered.set()
            await release.wait()
            return await super().step(data, query)

    task = asyncio.create_task(search_one(engine, Slow()))
    await asyncio.wait_for(entered.wait(), 2)
    try:

        async def cancel_and_edit():
            async with PostgresUnitOfWork(engine) as uow:
                job = await MusicSearches(uow.store, world.admin, 1).load(identifier)
                job.status = "cancelled"
                await Catalog(uow.store, world.admin).save(
                    world.group.id, {"title": "Обычная правка", "duration_seconds": 100}
                )

        await asyncio.wait_for(cancel_and_edit(), 2)
    finally:
        release.set()
        await task
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.status == "cancelled" and "tracks" not in job.data
        assert not await uow.store.find(Outbox, support_kind="music")


async def test_source_rate_limit_is_persisted_and_does_not_busy_retry(engine, world):
    identifier = await queued(engine, world)

    class Limited:
        async def step(self, data, query):
            raise SourceError(429, retry_after=120)

    await search_one(engine, Limited())
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.failures == 1 and job.lease_until is None
        assert job.next_attempt_at > now() + timedelta(seconds=115)
    assert not await search_one(engine, Limited())


async def test_revoked_admin_stops_work_before_contacting_a_provider(engine, world):
    identifier = await queued(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        rows = await uow.store.find(Membership, group_id=world.group.id, user_id=world.admin.id)
        rows[0].role = "member"
    source = Finish()
    await search_one(engine, source)
    assert source.calls == 0
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(MusicSearch, identifier)).status == "cancelled"


async def test_public_cache_never_copies_another_sessions_exclusions(engine, world):
    identifier = await queued(engine, world)
    await search_one(engine, Finish())
    async with PostgresUnitOfWork(engine) as uow:
        old = await uow.store.get(MusicSearch, identifier)
        old.data = {**old.data, "excluded": [0], "durations": {"0": 999}}
        old.status = "cancelled"
        service = MusicSearches(uow.store, world.admin, 1)
        new = await service.create(world.other.id, "Artist")
        new.status = "artists"
        new.data = {**new.data, "artists": [{"id": "123", "name": "Artist"}]}
        await service.choose_artist(new, 0)
        assert new.status == "choosing" and new.data["excluded"] == []
        assert service.selected(new)[0][1]["seconds"] == 180


async def test_unavailable_optional_provider_returns_partial_result_with_empty_bpm(engine, world):
    identifier = await queued(engine, world, phase="tunebat")
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        job.data = {
            **job.data,
            "raw": [{"title": "Новая", "seconds": 180, "source": "Apple Music", "bpm": None}],
        }

    class Unavailable:
        async def step(self, data, query):
            raise SourceError(403)

    await search_one(engine, Unavailable())
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.status == "choosing" and job.data["warnings"]
        assert job.data["tracks"][0]["bpm"] is None
