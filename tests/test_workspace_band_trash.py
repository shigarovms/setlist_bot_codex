from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_collective_workflows import client as telegram_client
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.trash import Trash
from setlist_bot.domain.entities import ChatBinding, Group, Outbox, PersonalDraft
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def action(client, engine, actor, session, operation, *, ok=True, **fields):
    payload = {**session, "action": operation, "request_id": str(uuid4()), **fields}
    pending = await post(client, actor, "band_trash_action", payload)
    assert pending["status"] == "pending"
    async with PostgresUnitOfWork(engine) as uow:
        events = await uow.store.find(Outbox, support_kind="catalog", sent=False)
        event = next(row for row in events if row.payload["request_id"] == pending["request"])
        assert event.group_id is None
    assert await deliver_request(engine, pending["request"]) is ok
    result = await get(client, actor, "catalog_status", **session, request=pending["request"])
    assert result["status"] == ("saved" if ok else "failed")
    assert await post(client, actor, "band_trash_action", payload) == result
    return result


async def test_band_trash_reuses_confirmed_deletion_remembers_data_and_guards_chat(engine, world):
    await setup_workspace(engine, world)
    band = str(world.group.id)
    send = telegram_client(engine)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.member)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"], private=True)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = {"session": (await post(client, 1, "open_account", {}))["session"]}
        member = {"session": (await post(client, 2, "open_account", {}))["session"]}
        old_scope = await post(client, 1, "open_repertoire", {"band": band})
        denial = await action(
            client, engine, 2, member, "preview_delete", ok=False, band=band, epoch=0
        )
        assert denial["error"] == "admin_only"
        preview = await action(client, engine, 1, admin, "preview_delete", band=band, epoch=0)
        assert preview["name"] == world.group.name
        denied = await action(
            client,
            engine,
            1,
            admin,
            "delete",
            ok=False,
            preview=preview["request"],
            name="Другой коллектив",
        )
        assert denied["error"] == "confirmation"
        result = await action(
            client, engine, 1, admin, "delete", preview=preview["request"], name=world.group.name
        )
        assert result["band"] == band
        assert (await get(client, 1, "bands"))["bands"] == [
            {"id": str(world.other.id), "name": world.other.name}
        ]
        assert not (await get(client, 2, "band_trash"))["bands"]
        (deleted,) = (await get(client, 1, "band_trash"))["bands"]
        assert deleted["id"] == band and deleted["name"] == world.group.name
        assert "Коллектив А" in (await send("/trash", private=True)).text
        blocked = await action(
            client, engine, 2, member, "restore", ok=False, band=band, epoch=deleted["epoch"]
        )
        assert blocked["error"] == "admin_only"
        async with PostgresUnitOfWork(engine) as uow:
            replacement = await Groups(uow.store, world.admin).create("Другой коллектив", -100)
            replacement_id = replacement.id
        occupied = await action(
            client, engine, 1, admin, "restore", ok=False, band=band, epoch=deleted["epoch"]
        )
        assert occupied["error"] == "chat_taken"
        async with PostgresUnitOfWork(engine) as uow:
            trash = Trash(uow.store, world.admin)
            await trash.delete_group(replacement_id, await trash.group_fingerprint(replacement_id))
        await action(client, engine, 1, admin, "restore", band=band, epoch=deleted["epoch"])
        await get(client, 1, "song", 403, **old_scope, song_id=str(world.song.id))
        async with PostgresUnitOfWork(engine) as uow:
            (binding,) = await uow.store.find(ChatBinding, chat_id=-100)
            assert binding.group_id == world.group.id
            restored = await uow.store.get(Group, world.group.id)
            assert restored.deleted_at is None
            assert (
                len(
                    await uow.store.find(
                        PersonalDraft, group_id=world.group.id, owner_id=world.member.id
                    )
                )
                == 1
            )
        assert all(row["id"] != band for row in (await get(client, 1, "band_trash"))["bands"])


async def test_band_trash_rejects_stale_preview_after_a_private_draft_edit(engine, world):
    await setup_workspace(engine, world)
    band = str(world.group.id)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = {"session": (await post(client, 1, "open_account", {}))["session"]}
        pending = await action(client, engine, 1, session, "preview_delete", band=band, epoch=0)
        async with PostgresUnitOfWork(engine) as uow:
            await Setlists(uow.store, world.member).edit_songs(
                world.group.id, world.concert.id, 0, ["Комета"], private=True
            )
        stale = await action(
            client,
            engine,
            1,
            session,
            "delete",
            ok=False,
            preview=pending["request"],
            name=world.group.name,
        )
        assert stale["error"] == "delete_changed"
        assert (await get(client, 1, "band_settings", band=band))["band"] == world.group.name
