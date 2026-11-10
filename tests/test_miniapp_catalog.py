"""Catalog writes remain in Catalog/bot, not in the public Mini App SQL role."""

import json
from uuid import UUID, uuid4

import pytest
from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import session, settings, signed, song
from test_telegram import FakeTelegram

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.miniapp_catalog import MiniCatalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import (
    CatalogProposal,
    Confirmation,
    DeploymentIdentity,
    Membership,
    Outbox,
    PersonalDraft,
    Song,
    SongAlias,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app


def payload(world, *, create=False):
    return {
        "request_id": str(uuid4()),
        "song_id": None if create else str(world.song.id),
        "expected": None if create else world.song.version,
        "fields": {
            "title": "Новая песня" if create else "Комета новая",
            "duration_seconds": "04:20",
            "bpm": 130,
            "lights": "синий, белый",
            "comment": "Тональность: Am\n<не HTML>",
            "aliases": ["Короткое имя", "Another name"],
        },
    }


@pytest.mark.usefixtures("invited_member")
@pytest.mark.parametrize("member", [False, True])
@pytest.mark.parametrize("create", [False, True])
async def test_catalog_queue_reuses_catalog_proposals_and_retry_receipts(
    engine, world, member, create
):
    identifier, _ = await session(engine, world, member)
    actor = world.member if member else world.admin
    request = payload(world, create=create)
    async with PostgresUnitOfWork(engine) as uow:
        drafts = MiniDrafts(uow.store, actor)
        initial = await drafts.state(identifier)
        await drafts.save(
            identifier,
            initial["version"],
            [
                song(world),
                {"kind": "pause", "duration_seconds": 60, "pause_label": ""},
                song(world),
            ],
            str(uuid4()),
        )
        before = (await uow.store.find(PersonalDraft, owner_id=actor.id))[0].content.copy()
        catalog = MiniCatalog(drafts)
        queued = await catalog.queue(identifier, request)
        assert await catalog.queue(identifier, request) == queued
        assert (await uow.store.get(Song, world.song.id)).version == 1
        assert len(await uow.store.find(Outbox, support_kind="catalog")) == 1
        conflict = {**request, "expected": 999}
        with pytest.raises(AppError, match="input"):
            await catalog.queue(identifier, conflict)
    telegram = FakeTelegram()
    async with Bot("123456:TEST", session=telegram) as bot:
        assert await deliver_one_outbox(bot, engine)
        assert not await deliver_one_outbox(bot, engine)
    assert not telegram.sent
    async with PostgresUnitOfWork(engine) as uow:
        catalog = MiniCatalog(MiniDrafts(uow.store, actor))
        result = await catalog.status(identifier, UUID(queued["request"]))
        assert result["status"] == "saved" and result["proposed"] == member
        assert await catalog.queue(identifier, request) == result
        assert (await uow.store.find(PersonalDraft, owner_id=actor.id))[0].content == before
        # Neither shared history nor stored draft is silently rewritten by a repertoire edit.
        assert not (await Setlists(uow.store, actor).read(world.group.id, world.concert.id))[0][
            "blocks"
        ][0]["items"]
        if member:
            proposal = (await uow.store.find(CatalogProposal))[0]
            assert proposal.payload["aliases"] == request["fields"]["aliases"]
            assert len(await uow.store.find(Song, group_id=world.group.id)) == 1
            await Catalog(uow.store, world.admin).decide(world.group.id, proposal.id, True)
            found = await Catalog(uow.store, world.admin).exact_song(
                world.group.id, request["fields"]["title"]
            )
        else:
            found = await uow.store.get(Song, UUID(result["song"]["id"]))
        assert found is not None
        assert (found.title, found.duration_seconds, found.bpm, found.comment) == (
            request["fields"]["title"],
            260,
            130,
            request["fields"]["comment"],
        )
        aliases = await uow.store.find(SongAlias, song_id=found.id)
        assert sorted(a.title for a in aliases) == sorted(request["fields"]["aliases"])


@pytest.mark.usefixtures("invited_member")
@pytest.mark.parametrize("change", ["version", "membership", "archived"])
async def test_worker_rechecks_catalog_version_access_and_archive(engine, world, change):
    identifier, _ = await session(engine, world, member=True)
    async with PostgresUnitOfWork(engine) as uow:
        queued = await MiniCatalog(MiniDrafts(uow.store, world.member)).queue(
            identifier, payload(world)
        )
    async with PostgresUnitOfWork(engine) as uow:
        if change == "version":
            original = await uow.store.get(Song, world.song.id)
            original.version += 1
        elif change == "archived":
            (await uow.store.get(Song, world.song.id)).archived = True
        else:
            (await uow.store.find(Membership, user_id=world.member.id, group_id=world.group.id))[
                0
            ].status = "pending"
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert not await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(Confirmation, UUID(queued["request"]))
        assert json.loads(row.command)["status"] == "failed"
        assert not await uow.store.find(CatalogProposal)
        assert (await uow.store.get(Song, world.song.id)).title == "Комета"


async def test_catalog_validation_tenant_and_alias_collision(engine, world):
    identifier, _ = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        catalog = MiniCatalog(MiniDrafts(uow.store, world.admin))
        for invalid in (
            {"song_id": str(world.other_song.id)},
            {"expected": 99},
            {"fields": {**payload(world)["fields"], "duration_seconds": "00:00"}},
            {"fields": {**payload(world)["fields"], "title": ""}},
            {"fields": {**payload(world)["fields"], "bpm": True}},
            {"fields": {**payload(world)["fields"], "aliases": ["комета новая"]}},
            {"fields": {**payload(world)["fields"], "aliases": ["Комета", " комета "]}},
        ):
            with pytest.raises(AppError):
                await catalog.queue(identifier, {**payload(world), **invalid})
        with pytest.raises(AppError):
            await catalog.read(identifier, world.other_song.id)
        with pytest.raises(AppError):
            await MiniCatalog(MiniDrafts(uow.store, world.member)).read(identifier, world.song.id)
        assert not await uow.store.find(Outbox, support_kind="catalog")


async def test_alias_replace_is_atomic_and_approval_revalidates_collisions(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        catalog = Catalog(uow.store, world.admin)
        data = {"title": "Комета", "duration_seconds": 218}
        saved = await catalog.save(world.group.id, data, world.song.id, 1, aliases=["Alias"])
        saved = await catalog.save(
            world.group.id, data, world.song.id, saved.version, aliases=["Alias", "Новый"]
        )
        assert sorted(a.title for a in await uow.store.find(SongAlias, song_id=world.song.id)) == [
            "Alias",
            "Новый",
        ]
        proposal = await Catalog(uow.store, world.member).save(
            world.group.id, data, world.song.id, saved.version, aliases=["Занято"]
        )
        await catalog.save(world.group.id, {"title": "Занято", "duration_seconds": 1})
        with pytest.raises(AppError, match="alias_collision"):
            await catalog.decide(world.group.id, proposal.id, True)
        assert proposal.status == "pending"
        assert saved.version == 3
        assert {a.title for a in await uow.store.find(SongAlias, song_id=world.song.id)} == {
            "Alias",
            "Новый",
        }


async def test_api_catalog_requires_signature_origin_and_session_owner(engine, world):
    identifier, _ = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        headers = {"Authorization": "tma " + signed(), "Origin": "https://example.org"}
        body = {"session": str(identifier), **payload(world)}
        for wrong in (
            {},
            {**headers, "Origin": "https://evil.example"},
            {**headers, "Authorization": "tma " + signed(actor=2)},
        ):
            response = await client.post("/miniapp/api/catalog_save", json=body, headers=wrong)
            assert response.status == 401
        response = await client.get(
            "/miniapp/api/song",
            params={"session": str(identifier), "song_id": str(world.song.id)},
            headers=headers,
        )
        assert response.status == 200 and (await response.json())["duration"] == "03:38"
        response = await client.post("/miniapp/api/catalog_save", json=body, headers=headers)
        assert response.status == 200, await response.text()
        result = await response.json()
        response = await client.get(
            "/miniapp/api/catalog_status",
            params={"session": str(identifier), "request": result["request"]},
            headers=headers,
        )
        assert response.status == 200 and (await response.json())["status"] == "pending"


async def test_parallel_requests_and_intervening_alias_are_not_lost(engine, world):
    import asyncio

    identifier, _ = await session(engine, world)
    data = payload(world)

    async def enqueue():
        async with PostgresUnitOfWork(engine) as uow:
            return await MiniCatalog(MiniDrafts(uow.store, world.admin)).queue(identifier, data)

    first, second = await asyncio.gather(enqueue(), enqueue())
    assert first == second
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).alias(
            world.group.id, world.song.id, "Новое сокращение"
        )
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert not await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        result = await MiniCatalog(MiniDrafts(uow.store, world.admin)).status(
            identifier, UUID(first["request"])
        )
        assert result["status"] == "failed" and result["error"] == "conflict"
        assert [a.title for a in await uow.store.find(SongAlias, song_id=world.song.id)] == [
            "Новое сокращение"
        ]
        assert len(await uow.store.find(Outbox, support_kind="catalog")) == 1


async def test_catalog_edit_preserves_approved_history_and_trash_removes_receipts(engine, world):
    from datetime import timedelta

    from setlist_bot.application.trash import RETENTION, Trash, TrashCleanup
    from setlist_bot.domain.entities import now

    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await lists.approve(world.group.id, world.concert.id, 1)
        approved = await lists.read(world.group.id, world.concert.id)
    identifier, _ = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        queued = await MiniCatalog(MiniDrafts(uow.store, world.admin)).queue(
            identifier, payload(world)
        )
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
            == approved
        )
        trash = Trash(uow.store, world.admin)
        fingerprint = await trash.concert_fingerprint(world.group.id, world.concert.id)
        await trash.delete_concert(world.group.id, world.concert.id, fingerprint)
    async with PostgresUnitOfWork(engine) as uow:
        await TrashCleanup(uow.store).run(at=now() + RETENTION + timedelta(seconds=1))
        assert not await uow.store.find(Confirmation, id=UUID(queued["request"]))


@pytest.mark.usefixtures("invited_member")
@pytest.mark.parametrize("intervening_change", [None, "version", "role"])
async def test_catalog_archive_rechecks_version_and_role_and_preserves_setlist(
    engine, world, intervening_change
):
    request = {
        "request_id": str(uuid4()),
        "song_id": str(world.song.id),
        "expected": world.song.version,
    }
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await lists.approve(world.group.id, world.concert.id, 1)
        approved = await lists.read(world.group.id, world.concert.id)
    member, _ = await session(engine, world, member=True)
    async with PostgresUnitOfWork(engine) as uow:
        catalog = MiniCatalog(MiniDrafts(uow.store, world.member))
        with pytest.raises(AppError, match="admin_only"):
            await catalog.queue(member, request, archive=True)
        if intervening_change == "role":
            (await uow.store.find(Membership, user_id=world.member.id))[0].role = "admin"
    actor = world.member if intervening_change == "role" else world.admin
    identifier = member if intervening_change == "role" else (await session(engine, world))[0]
    async with PostgresUnitOfWork(engine) as uow:
        catalog = MiniCatalog(MiniDrafts(uow.store, actor))
        queued = await catalog.queue(identifier, request, archive=True)
        assert await catalog.queue(identifier, request, archive=True) == queued
        assert not (await uow.store.get(Song, world.song.id)).archived
        assert len(await uow.store.find(Outbox, support_kind="catalog")) == 1
        if intervening_change == "role":
            (await uow.store.find(Membership, user_id=actor.id))[0].role = "member"
        if intervening_change == "version":
            await Catalog(uow.store, actor).alias(world.group.id, world.song.id, "Другой вариант")
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine) is (intervening_change is None)
    async with PostgresUnitOfWork(engine) as uow:
        drafts = MiniDrafts(uow.store, actor)
        catalog = MiniCatalog(drafts)
        receipt = await catalog.status(identifier, UUID(queued["request"]))
        stored = await uow.store.get(Song, world.song.id)
        assert stored.archived is (intervening_change is None)
        if intervening_change is None:
            assert receipt["status"] == "saved" and receipt["archived"]
            assert await catalog.queue(identifier, request, archive=True) == receipt
            assert str(world.song.id) not in {
                row["id"] for row in (await drafts.state(identifier))["catalog"]
            }
            assert (
                await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
                == approved
            )
            await Catalog(uow.store, actor).archive(
                world.group.id, world.song.id, stored.version, archived=False
            )
            assert not stored.archived
        else:
            assert receipt["status"] == "failed"
            assert receipt["error"] == (
                "admin_only" if intervening_change == "role" else "conflict"
            )


@pytest.mark.usefixtures("invited_member")
@pytest.mark.parametrize(("value", "seconds"), [("45", 2700), ("1:15", 4500), ("-", None)])
async def test_planned_duration_uses_versioned_worker_and_keeps_draft(
    engine, world, value, seconds
):
    from setlist_bot.domain.entities import Concert

    identifier, initial = await session(engine, world)
    member, _ = await session(engine, world, member=True)
    request = {"request_id": str(uuid4()), "expected": initial["metadata_version"], "value": value}
    async with PostgresUnitOfWork(engine) as uow:
        catalog = MiniCatalog(MiniDrafts(uow.store, world.member))
        with pytest.raises(AppError, match="admin_only"):
            await catalog.queue(member, request, planned_duration=True)
        catalog = MiniCatalog(MiniDrafts(uow.store, world.admin))
        queued = await catalog.queue(identifier, request, planned_duration=True)
        assert (await uow.store.get(Concert, world.concert.id)).metadata_version == initial[
            "metadata_version"
        ]
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        catalog = MiniCatalog(service)
        receipt = await catalog.queue(identifier, request, planned_duration=True)
        assert receipt["request"] == queued["request"]
        assert receipt["status"] == "saved" and receipt["planned_seconds"] == seconds
        updated = await service.state(identifier)
        assert updated["version"] == initial["version"] and updated["entries"] == initial["entries"]
        assert updated["metadata_version"] == initial["metadata_version"] + 1
        with pytest.raises(AppError, match="conflict"):
            await catalog.queue(
                identifier, {**request, "request_id": str(uuid4())}, planned_duration=True
            )


@pytest.mark.usefixtures("invited_member")
async def test_planned_duration_worker_rechecks_role_after_queue(engine, world):
    identifier, initial = await session(engine, world)
    # Grant an additional member admin rights only while queuing.
    async with PostgresUnitOfWork(engine) as uow:
        membership = (await uow.store.find(Membership, user_id=world.member.id))[0]
        membership.role = "admin"
    member, _ = await session(engine, world, member=True)
    async with PostgresUnitOfWork(engine) as uow:
        request = {
            "request_id": str(uuid4()),
            "expected": initial["metadata_version"],
            "value": "45",
        }
        queued = await MiniCatalog(MiniDrafts(uow.store, world.member)).queue(
            member, request, planned_duration=True
        )
        (await uow.store.find(Membership, user_id=world.member.id))[0].role = "member"
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert not await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        receipt = await MiniCatalog(MiniDrafts(uow.store, world.member)).status(
            member, UUID(queued["request"])
        )
        assert receipt["status"] == "failed" and receipt["error"] == "admin_only"
        assert (await MiniDrafts(uow.store, world.admin).state(identifier))[
            "planned_seconds"
        ] is None
