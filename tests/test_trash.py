import asyncio
import io
import json
from datetime import timedelta
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest

from setlist_bot.application.backup import Backup
from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.templates import Templates
from setlist_bot.application.trash import Trash, TrashCleanup
from setlist_bot.domain import entities as e
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.telegram import Processor


def sender(engine):
    processor, ids = Processor(engine), count(1)

    async def send(command, actor=1, private=False, chat=-100):
        return await processor.process(
            next(ids),
            actor,
            actor if private else chat,
            command,
            private=private,
            display_name=f"Музыкант {actor}",
        )

    return send


def button(response, prefix):
    return next(data for _, data in response.buttons if data.startswith(prefix))


async def seed_concert(uow, world):
    service = Setlists(uow.store, world.admin)
    await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"], replace=True)
    await service.approve(world.group.id, world.concert.id, 1)
    personal = Setlists(uow.store, world.member)
    await personal.read(world.group.id, world.concert.id, private=True)
    draft = (await uow.store.find(e.PersonalDraft, owner_id=world.member.id))[0]
    draft.content = {**draft.content, "private_note": "СЕКРЕТНЫЙ ЧЕРНОВИК"}
    snapshot, _, _ = await service.read(world.group.id, world.concert.id)
    submission = e.Submission(
        group_id=world.group.id,
        concert_id=world.concert.id,
        author_id=world.member.id,
        snapshot=snapshot,
    )
    uow.store.add(submission)
    Templates(uow.store, world.admin, -100).create(
        world.group.id, world.concert.id, snapshot, private=False
    )
    uow.store.add(
        e.Outbox(
            chat_id=-100,
            text="proposal",
            group_id=world.group.id,
            concert_id=world.concert.id,
            buttons=[["open", f"view_submission {submission.id}"]],
        )
    )


async def test_concert_delete_confirmation_hides_all_variants_and_restores_private_drafts(
    engine, world
):
    async with PostgresUnitOfWork(engine) as uow:
        await seed_concert(uow, world)
        original = await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    await send(f"/use_concert {world.concert.id}", actor=2)
    preview = await send("/delete_concert")
    assert "всеми версиями" in preview.text and "30 дней" in preview.text
    assert "администратора" in (await send("/delete_concert", actor=2)).text
    assert "Подтверждение" in (await send(button(preview, "confirm "), actor=2)).text
    deleted = await send(button(preview, "confirm "))
    assert "перемещено в корзину" in deleted.text
    assert "Новый год" not in (await send("/concerts")).text
    assert "удалён" in (await send(f"/use_concert {world.concert.id}", actor=2)).text
    assert not (await send("/trash", actor=2)).buttons
    trash = await send("/trash", private=True)
    restored = await send(button(trash, "restore_concert "), private=True)
    assert "восстановлен" in restored.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
            == original
        )
        draft = (await uow.store.find(e.PersonalDraft, owner_id=world.member.id))[0]
        assert draft.content["private_note"] == "СЕКРЕТНЫЙ ЧЕРНОВИК"
        assert len(await uow.store.find(e.Song, group_id=world.group.id)) == 1
        assert all(event.sent for event in await uow.store.find(e.Outbox, group_id=world.group.id))


async def test_group_delete_has_backup_two_confirmations_and_preserves_other_groups(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await seed_concert(uow, world)
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    preview = await send("/delete_group")
    exported = await send(button(preview, "backup_group "))
    assert exported.files
    with ZipFile(io.BytesIO(exported.files[0][1])) as archive:
        data = json.loads(archive.read("data.json"))
        assert data["version"] == 1 and data["private_drafts_included"] is False
        assert "songs.csv" in archive.namelist() and "revisions.csv" in archive.namelist()
        assert "СЕКРЕТНЫЙ ЧЕРНОВИК" not in archive.read("data.json").decode()
        assert "telegram_id" not in archive.read("data.json").decode()
    first = await send(button(preview, "confirm "))
    assert "Последнее подтверждение" in first.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(e.Group, world.group.id)).deleted_at is None
    deleted = await send(button(first, "confirm "))
    assert "Чат доступен" in deleted.text
    groups = await send("/groups", private=True)
    assert all(label != world.group.name for label, _ in groups.buttons)
    assert any(label == world.other.name for label, _ in groups.buttons)
    trash = await send("/trash", private=True)
    restore = button(trash, "restore_group ")
    assert "администратора" in (await send(restore, actor=2, private=True)).text
    assert "восстановлен" in (await send(restore, private=True)).text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await Groups(uow.store, world.member).access(world.group.id)).deleted_at is None
        assert len(await uow.store.find(e.ChatBinding, chat_id=-100)) == 1
        assert len(await uow.store.find(e.User)) == 3
        assert len(await uow.store.find(e.Song, group_id=world.other.id)) == 1


async def test_delete_preview_is_rejected_when_data_changes(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    preview = await send("/delete_concert")
    await send("/add Комета")
    assert "Данные изменились" in (await send(button(preview, "confirm "))).text
    preview = await send("/delete_group")
    first = await send(button(preview, "confirm "))
    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.member).edit(
            world.group.id, world.concert.id, 0, "default_pause", ["30"], private=True
        )
    assert "Данные изменились" in (await send(button(first, "confirm "))).text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(e.Group, world.group.id)).deleted_at is None


async def test_chat_can_be_reused_but_restoration_cannot_overwrite_new_group(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        await trash.delete_group(world.group.id, await trash.group_fingerprint(world.group.id))
        await uow.store.flush()
        replacement = await Groups(uow.store, world.admin).create("Новый коллектив", -100)
        with pytest.raises(AppError, match="chat_taken"):
            await trash.restore_group(world.group.id, 1)
        assert (await uow.store.find(e.ChatBinding, chat_id=-100))[0].group_id == replacement.id
        await trash.delete_group(replacement.id, await trash.group_fingerprint(replacement.id))
        await uow.store.flush()
        await trash.restore_group(world.group.id, 1)
        assert (await uow.store.find(e.ChatBinding, chat_id=-100))[0].group_id == world.group.id


@pytest.mark.parametrize("scope", ["concert", "group"])
async def test_purge_removes_expired_tree_only_and_keeps_accounts(engine, world, scope):
    async with PostgresUnitOfWork(engine) as uow:
        await seed_concert(uow, world)
        trash = Trash(uow.store, world.admin)
        if scope == "group":
            target = await trash.delete_group(
                world.group.id, await trash.group_fingerprint(world.group.id)
            )
        else:
            target = await trash.delete_concert(
                world.group.id,
                world.concert.id,
                await trash.concert_fingerprint(world.group.id, world.concert.id),
            )
        target.deleted_at = e.now() - timedelta(days=31)
        await uow.store.flush()
        with pytest.raises(AppError, match="trash_expired"):
            if scope == "group":
                await trash.restore_group(world.group.id, 1)
            else:
                await trash.restore_concert(world.group.id, world.concert.id, 1)
        await TrashCleanup(uow.store).run()
        assert not await uow.store.find(e.Setlist, concert_id=world.concert.id)
        assert not await uow.store.find(e.PersonalDraft, concert_id=world.concert.id)
        assert not await uow.store.find(e.Submission, concert_id=world.concert.id)
        assert not await uow.store.find(e.SongListTemplate, concert_id=world.concert.id)
        assert len(await uow.store.find(e.User)) == 3
        assert len(await uow.store.find(e.Song, group_id=world.other.id)) == 1
        if scope == "group":
            assert not await uow.store.find(e.Song, group_id=world.group.id)
            assert not await uow.store.find(e.Membership, group_id=world.group.id)
        else:
            assert len(await uow.store.find(e.Song, group_id=world.group.id)) == 1
            assert len(await uow.store.find(e.Concert, group_id=world.group.id)) == 1


async def test_old_confirmation_cannot_apply_after_delete_and_restore(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    song = await send("/song Новая  00:05")
    deleted = await send("/delete_group")
    second = await send(button(deleted, "confirm "))
    await send(button(second, "confirm "))
    trash = await send("/trash", private=True)
    await send(button(trash, "restore_group "), private=True)
    await send(f"/use_concert {world.concert.id}")
    assert "Подтверждение" in (await send(button(song, "confirm "))).text


@pytest.mark.parametrize("scope", ["concert", "group"])
async def test_outbox_never_delivers_deleted_data(engine, world, scope):
    async with PostgresUnitOfWork(engine) as uow:
        event = e.Outbox(
            chat_id=-100, text="hidden", group_id=world.group.id, concert_id=world.concert.id
        )
        uow.store.add(event)
        await uow.store.flush()
        trash = Trash(uow.store, world.admin)
        if scope == "group":
            await trash.delete_group(world.group.id, await trash.group_fingerprint(world.group.id))
        else:
            await trash.delete_concert(
                world.group.id,
                world.concert.id,
                await trash.concert_fingerprint(world.group.id, world.concert.id),
            )
        event.sent = False  # Even an old queued row cannot bypass delivery checks.
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    await deliver_one_outbox(bot, engine)
    bot.send_message.assert_not_awaited()
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(e.Outbox, event.id)).sent


async def test_concurrent_deletion_and_edit_cannot_lose_an_unseen_change(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        fingerprint = await Trash(uow.store, world.admin).concert_fingerprint(
            world.group.id, world.concert.id
        )

    async def act(delete):
        try:
            async with PostgresUnitOfWork(engine) as uow:
                if delete:
                    await Trash(uow.store, world.admin).delete_concert(
                        world.group.id, world.concert.id, fingerprint
                    )
                else:
                    await Setlists(uow.store, world.member).edit_songs(
                        world.group.id, world.concert.id, 0, ["Комета"]
                    )
            return "deleted" if delete else "edited"
        except AppError as exc:
            return exc.code

    outcomes = await asyncio.gather(act(True), act(False))
    assert outcomes in (["deleted", "in_trash"], ["delete_changed", "edited"])


async def test_backup_is_admin_only_and_csv_formulae_are_inert(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "=1+1", "duration_seconds": 5}
        )
        with pytest.raises(AppError, match="admin_only"):
            await Backup(uow.store, world.member).build(world.group.id)
        with ZipFile(
            io.BytesIO(await Backup(uow.store, world.admin).build(world.group.id))
        ) as archive:
            assert "'=1+1" in archive.read("songs.csv").decode("utf-8-sig")
