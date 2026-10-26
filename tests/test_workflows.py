import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Block, CatalogProposal, Item, Membership, Outbox
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def test_registration_requires_approval_and_supports_multiple_groups(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        pending = await Groups(uow.store, world.member).register(world.other.id, "басист")
        await uow.store.flush()
        with pytest.raises(AppError, match="forbidden"):
            await Catalog(uow.store, world.member).list_songs(world.other.id)
        await Groups(uow.store, world.admin).decide_member(world.other.id, pending.id, True)
        assert pending.role == "member" and pending.musical_role == "басист"
        assert len(await Catalog(uow.store, world.member).list_songs(world.other.id)) == 1
        assert len(await uow.store.find(Membership, user_id=world.member.id, status="active")) == 2


async def test_catalog_proposals_and_rights(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        member = Catalog(uow.store, world.member)
        proposal = await member.save(world.group.id, {"title": "Интро", "duration_seconds": 5})
        assert isinstance(proposal, CatalogProposal)
        await uow.store.flush()
        assert len(await member.list_songs(world.group.id)) == 1
        with pytest.raises(AppError, match="admin_only"):
            await member.decide(world.group.id, proposal.id, True)
        await Catalog(uow.store, world.admin).decide(world.group.id, proposal.id, True)
        assert len(await member.list_songs(world.group.id)) == 2
        with pytest.raises(AppError, match="not_found"):
            await member.song(world.group.id, world.other_song.id)
        with pytest.raises(AppError, match="forbidden"):
            await Catalog(uow.store, world.stranger).list_songs(world.group.id)


async def test_aliases_fuzzy_search_and_collision(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        catalog = Catalog(uow.store, world.admin)
        proposal = await Catalog(uow.store, world.member).alias(
            world.group.id, world.song.id, "вечеринки"
        )
        await uow.store.flush()
        await catalog.decide(world.group.id, proposal.id, True)
        for query in (" ВЕЧЕРИНКИ ", "kometa", "комтеа"):
            assert [s.id for s in await catalog.search(world.group.id, query)] == [world.song.id]
        other = await catalog.save(world.group.id, {"title": "Другая", "duration_seconds": 10})
        with pytest.raises(AppError, match="alias_collision"):
            await catalog.alias(world.group.id, other.id, "Комета")


async def test_approved_snapshot_is_immutable_and_members_cannot_approve(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        admin = Setlists(uow.store, world.admin)
        member = Setlists(uow.store, world.member)
        await member.edit(world.group.id, world.concert.id, 0, "add", ["1", str(world.song.id)])
        with pytest.raises(AppError, match="admin_only"):
            await member.approve(world.group.id, world.concert.id, 1)
        await admin.approve(world.group.id, world.concert.id, 1)
        before, version, approved = await admin.read(world.group.id, world.concert.id)
        assert approved and version == 2
        song = await Catalog(uow.store, world.admin).song(world.group.id, world.song.id)
        await Catalog(uow.store, world.admin).save(
            world.group.id,
            {
                "title": "Новое название",
                "duration_seconds": 500,
            },
            song.id,
            song.version,
        )
        after, _, _ = await admin.read(world.group.id, world.concert.id)
        assert before == after and after["blocks"][0]["items"][0]["title"] == "Комета"
        with pytest.raises(AppError, match="approved"):
            await member.edit(world.group.id, world.concert.id, 2, "block", ["Антракт"])
        await admin.reopen(world.group.id, world.concert.id, 2)
        current, version, approved = await admin.read(world.group.id, world.concert.id)
        assert not approved and current["blocks"][0]["items"][0]["title"] == "Новое название"
        history = await admin.history(world.group.id, world.concert.id)
        frozen = next(r for r in history if r.action == "approved")
        assert frozen.snapshot == before
        await admin.approve(world.group.id, world.concert.id, version)
        assert (
            len(
                [
                    r
                    for r in await admin.history(world.group.id, world.concert.id)
                    if r.action == "approved"
                ]
            )
            == 2
        )


async def test_concurrent_edits_do_not_overwrite(engine, world):
    async def edit(user):
        try:
            async with PostgresUnitOfWork(engine) as uow:
                await Setlists(uow.store, user).edit(
                    world.group.id, world.concert.id, 0, "add", ["1", str(world.song.id)]
                )
            return "ok"
        except AppError as exc:
            return exc.code

    assert sorted(await asyncio.gather(edit(world.admin), edit(world.member))) == ["conflict", "ok"]
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, version, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert version == 1 and len(snapshot["blocks"][0]["items"]) == 1


async def test_personal_drafts_are_persistent_private_and_separate(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        member = Setlists(uow.store, world.member)
        await member.edit(
            world.group.id, world.concert.id, 0, "add", ["1", str(world.song.id)], private=True
        )
    async with PostgresUnitOfWork(engine) as uow:
        member = Setlists(uow.store, world.member)
        own, _, _ = await member.read(world.group.id, world.concert.id, private=True)
        shared, _, _ = await member.read(world.group.id, world.concert.id)
        admin, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id, private=True
        )
        different, _, _ = await member.read(world.group.id, world.concert2.id, private=True)
        assert len(own["blocks"][0]["items"]) == 1
        assert all(not s["blocks"][0]["items"] for s in (shared, admin, different))
        submission = await member.publish(world.group.id, world.concert.id)
        await uow.store.flush()
        await member.edit(world.group.id, world.concert.id, 1, "remove", ["1", "1"], private=True)
        assert len(submission.snapshot["blocks"][0]["items"]) == 1
        await member.apply(world.group.id, world.concert.id, submission.id, 0)
        common, _, _ = await member.read(world.group.id, world.concert.id)
        assert len(common["blocks"][0]["items"]) == 1


async def test_archived_songs_stay_in_history_and_cannot_be_added(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        setlists = Setlists(uow.store, world.admin)
        await setlists.edit(world.group.id, world.concert.id, 0, "add", ["1", str(world.song.id)])
        catalog = Catalog(uow.store, world.admin)
        await catalog.archive(world.group.id, world.song.id, 1, True)
        assert not await catalog.list_songs(world.group.id)
        assert len(await catalog.list_songs(world.group.id, archived=True)) == 1
        assert (
            len((await setlists.read(world.group.id, world.concert.id))[0]["blocks"][0]["items"])
            == 1
        )
        with pytest.raises(AppError, match="archived"):
            await setlists.edit(
                world.group.id, world.concert2.id, 0, "add", ["1", str(world.song.id)]
            )
        await catalog.archive(world.group.id, world.song.id, 2, False)
        assert len(await catalog.list_songs(world.group.id)) == 1


async def test_database_rejects_cross_group_song_reference(engine, world):
    with pytest.raises(IntegrityError):
        async with PostgresUnitOfWork(engine) as uow:
            setlist = await Setlists(uow.store, world.admin).load(world.group.id, world.concert.id)
            block = (await uow.store.find(Block, setlist_id=setlist.id))[0]
            uow.store.add(
                Item(
                    group_id=world.group.id,
                    block_id=block.id,
                    song_id=world.other_song.id,
                    position=1,
                )
            )


async def test_restore_order_records_new_revision(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        initial = (await service.history(world.group.id, world.concert.id))[0]
        await service.edit(world.group.id, world.concert.id, 0, "add", ["1", str(world.song.id)])
        await service.restore(world.group.id, world.concert.id, initial.id, 1)
        content, version, _ = await service.read(world.group.id, world.concert.id)
        assert version == 2 and not content["blocks"][0]["items"]
        assert (await service.history(world.group.id, world.concert.id))[0].action == "restored"


async def test_processor_idempotency_and_group_admin_check(engine):
    processor = Processor(engine)
    denied = await processor.process(1, 123, -123, "/group Test", private=False)
    assert "администратор" in denied.text
    result = await processor.process(2, 123, -123, "/group Test", private=False, chat_admin=True)
    assert "Создан" in result.text
    assert (
        await processor.process(2, 123, -123, "/group Test", private=False, chat_admin=True) is None
    )


async def test_confirmation_bound_to_actor_chat_group_and_concert(engine, world):
    processor = Processor(engine)
    await processor.process(1, 1, -100, f"/use_concert {world.concert.id}", private=False)
    preview = await processor.process(2, 1, -100, "/song Новая  00:05", private=False)
    confirmation = preview.buttons[0][1]
    denied = await processor.process(3, 2, -100, confirmation, private=False)
    assert "Подтверждение" in denied.text
    accepted = await processor.process(4, 1, -100, confirmation, private=False)
    assert "сохранена" in accepted.text
    assert "не найден" in (await processor.process(5, 1, -100, confirmation, private=False)).text
    await processor.process(6, 1, -100, f"/add {world.song.id}", private=False)
    remove = await processor.process(7, 1, -100, "/remove 1", private=False)
    await processor.process(8, 1, -100, f"/use_concert {world.concert2.id}", private=False)
    wrong = await processor.process(9, 1, -100, remove.buttons[0][1], private=False)
    assert "Подтверждение" in wrong.text


async def test_processor_conflict_returns_current_setlist(engine, world):
    processor = Processor(engine)
    for update, actor in ((1, 1), (2, 2)):
        await processor.process(
            update, actor, -100, f"/use_concert {world.concert.id}", private=False
        )
    await processor.process(3, 1, -100, f"/add {world.song.id}", private=False)
    conflict = await processor.process(4, 2, -100, f"/add {world.song.id}", private=False)
    assert "уже изменились" in conflict.text and "Комета" in conflict.text
    assert "Версия 1" in conflict.text


async def test_private_publish_is_explicit_and_uses_outbox(engine, world):
    processor = Processor(engine)
    await processor.process(1, 2, 2, f"/use_group {world.group.id}", private=True)
    await processor.process(2, 2, 2, f"/use_concert {world.concert.id}", private=True)
    await processor.process(3, 2, 2, f"/add {world.song.id}", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Outbox)
    await processor.process(4, 2, 2, "/publish", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        events = await uow.store.find(Outbox)
        assert len(events) == 1 and events[0].chat_id == -100 and "Комета" in events[0].text


async def test_unknown_identifiers_do_not_leak_other_group(engine, world):
    processor = Processor(engine)
    await processor.process(1, 2, -100, f"/use_concert {world.concert.id}", private=False)
    for i, command in enumerate(
        (
            f"/use_concert {world.other_concert.id}",
            f"/add {world.other_song.id}",
            f"/revision {uuid4()}",
        ),
        2,
    ):
        response = await processor.process(i, 2, -100, command, private=False)
        assert "не найден" in response.text


async def test_copy_is_independent_and_previewed(engine, world):
    processor = Processor(engine)
    result = await processor.process(
        1, 1, -100, f"/copy {world.song.id}  Комета расширенная версия  05:00", private=False
    )
    assert "05:00" in result.text and "110 BPM" in result.text and result.buttons
    await processor.process(2, 1, -100, result.buttons[0][1], private=False)
    async with PostgresUnitOfWork(engine) as uow:
        songs = await Catalog(uow.store, world.admin).list_songs(world.group.id)
        assert len(songs) == 2
        assert {s.duration_seconds for s in songs} == {218, 300}
        assert all(s.bpm == 110 and s.lights == "белый, красный" for s in songs)
