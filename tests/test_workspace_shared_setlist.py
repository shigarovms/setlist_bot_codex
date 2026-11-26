from datetime import UTC, datetime, timedelta
from uuid import uuid4

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_setlist_menus import sender
from test_telegram import FakeTelegram

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import (
    DeploymentIdentity,
    Membership,
    Outbox,
    PersonalDraft,
    Submission,
)
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app
from setlist_bot.transport.publications import queue_publication


def scope(world):
    return {"band": str(world.group.id), "performance": str(world.concert.id)}


async def action(client, engine, actor, session, which, state, *, success=True, **kwargs):
    payload = {
        **session,
        "action": which,
        "version": state["version"],
        "token": state["token"],
        "request_id": str(uuid4()),
        **kwargs,
    }
    queued = await post(client, actor, "shared_action", payload)
    assert queued["status"] == "pending"
    assert await deliver_request(engine, queued["request"]) is success
    receipt = await get(client, actor, "catalog_status", **session, request=queued["request"])
    assert await post(client, actor, "shared_action", payload) == receipt
    return receipt


async def setup_workspace(engine, world):
    from conftest import invite_fixture_member

    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        await invite_fixture_member(uow.store, world.concert, world.member)


async def test_shared_edit_conflicts_and_refreshes_without_creating_drafts(engine, world):
    await setup_workspace(engine, world)
    telegram = sender(engine)
    await telegram(f"/use_concert {world.concert.id}", remember=731)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        await get(client, 3, "shared_editor", 403, **query)
        hidden = {**query, "performance": str(world.concert2.id)}
        await post(client, 2, "open_shared", hidden, 403)
        session = await post(client, 1, "open_draft", query)
        initial = await get(client, 1, "draft_shared", **session)
        participant = await post(client, 2, "open_draft", query)
        await get(client, 2, "draft_shared", 403, **participant)
        song = {"kind": "song", "song_id": str(world.song.id), "version": 1, "pause_before": None}
        items = [song, {"kind": "pause", "duration_seconds": 35, "pause_label": "Перерыв"}, song]
        saved = await action(
            client, engine, 1, session, "edit", initial, entries=items, default_pause_seconds=9
        )
        assert saved["status"] == "saved" and not saved["requested"]
        latest = (await get(client, 1, "performance", **query))["setlist"]
        assert latest["seconds"] == 471 and latest["entries"][1]["label"] == "Перерыв"
        stale = await action(
            client,
            engine,
            1,
            session,
            "edit",
            initial,
            success=False,
            entries=[],
            default_pause_seconds=0,
        )
        assert stale["error"] == "conflict"
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(PersonalDraft)
            assert await uow.store.find(Outbox, support_kind="refresh", concert_id=world.concert.id)
            catalog = Catalog(uow.store, world.admin)
            await catalog.save(
                world.group.id, {"title": "Комета", "duration_seconds": 220}, world.song.id, 1
            )
        stale = await action(
            client,
            engine,
            1,
            session,
            "edit",
            latest,
            success=False,
            entries=[],
            default_pause_seconds=0,
        )
        assert stale["error"] == "conflict"


async def test_variants_apply_and_requests_for_approval_and_reopening(engine, world):
    await setup_workspace(engine, world)
    telegram = sender(engine)
    group_id, concert_id = world.group.id, world.concert.id
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.member)
        await lists.edit_songs(group_id, concert_id, 0, ["Комета", "Комета"], private=True)
        await queue_publication(uow.store, world.member, group_id, concert_id, 1)
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        member = await post(client, 2, "open_shared", query)
        admin = await post(client, 1, "open_shared", query)
        variants = (await get(client, 2, "shared_variants", **query))["variants"]
        assert len(variants) == 1 and not variants[0]["applied"]
        variant = variants[0]["id"]
        preview = await get(
            client, 2, "shared_variant", **query, kind="submission", variant=variant
        )
        assert [entry["title"] for entry in preview["entries"]] == ["Комета", "Комета"]
        before = (await get(client, 1, "performance", **query))["setlist"]
        inbox = await get(client, 1, "shared_requests", **query)
        assert [row["id"] for row in inbox["proposals"]] == [variant]
        await post(
            client,
            2,
            "shared_action",
            {
                **member,
                "action": "apply",
                "version": before["version"],
                "token": before["token"],
                "request_id": str(uuid4()),
                "variant": variant,
            },
            403,
        )
        await telegram(f"apply_submission {variant}", actor=2, chat=-100)
        assert (await get(client, 1, "performance", **query))["setlist"] == before
        await telegram(f"apply_submission {variant}", chat=-100)
        assert not (await get(client, 1, "shared_requests", **query))["proposals"]
        applied = (await get(client, 1, "performance", **query))["setlist"]
        assert applied["seconds"] == 441 and not applied["approved"]
        wanted = await action(client, engine, 2, member, "approve", applied)
        assert wanted["requested"] and not wanted["duplicate"] and "notify" not in wanted
        duplicate = await action(client, engine, 2, member, "approve", applied)
        assert duplicate["duplicate"]
        async with PostgresUnitOfWork(engine) as uow:
            rows = await uow.store.find(Outbox, group_id=group_id, chat_id=world.admin.telegram_id)
            notices = [
                row for row in rows if row.buttons and row.buttons[0][1].startswith("slreq ")
            ]
            assert len(notices) == 1
            callback = notices[0].buttons[0][1]
        await telegram(callback, private=True, chat=1)
        approved = (await get(client, 2, "performance", **query))["setlist"]
        assert approved["approved"]
        await get(client, 2, "shared_editor", 403, **query)
        wanted = await action(client, engine, 2, member, "reopen", approved)
        assert wanted["requested"]
        await get(client, 2, "shared_requests", 403, **query)
        requests = (await get(client, 1, "shared_requests", **query))["requests"]
        assert len(requests) == 1 and requests[0]["operation"] == "reopen"
        decision = await action(
            client, engine, 1, admin, "decide", approved, request=requests[0]["id"], accept=False
        )
        assert decision["decision"] is False and "resolved" not in decision
        assert (await get(client, 1, "shared_requests", **query))["requests"] == []
        async with PostgresUnitOfWork(engine) as uow:
            notices = await uow.store.find(
                Outbox, support_kind="sl_result", chat_id=world.member.telegram_id
            )
            assert len(notices) == 1
            notice_id = notices[0].id
        fake = FakeTelegram()
        async with Bot("123456:TEST", session=fake) as bot:
            for _ in range(10):
                await deliver_one_outbox(bot, engine)
                async with PostgresUnitOfWork(engine) as uow:
                    if (await uow.store.get(Outbox, notice_id)).delivered_at is not None:
                        break
            else:
                raise AssertionError("The decision was not delivered to the requester")
        await action(client, engine, 1, admin, "reopen", approved)
        reopened = (await get(client, 2, "performance", **query))["setlist"]
        assert not reopened["approved"] and reopened["version"] > approved["version"]
        history = (await get(client, 2, "shared_history", **query))["variants"]
        assert [row["action"] for row in history] == [
            "reopened",
            "approved",
            "proposal_applied",
            "created",
        ]
        initial = next(row for row in history if row["action"] == "created")
        snapshot = await get(
            client, 2, "shared_variant", **query, kind="revision", variant=initial["id"]
        )
        assert snapshot["entries"] == []
        await action(client, engine, 2, member, "restore", reopened, variant=initial["id"])
        restored = (await get(client, 2, "performance", **query))["setlist"]
        assert restored["version"] == reopened["version"] + 1 and restored["entries"] == []
        async with PostgresUnitOfWork(engine) as uow:
            _, version, _ = await Setlists(uow.store, world.member).read(
                group_id, concert_id, private=True
            )
            assert version == 1


async def test_declining_proposal_closes_telegram_action_and_survives_retry(engine, world):
    await setup_workspace(engine, world)
    group, concert = world.group.id, world.concert.id
    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.member).edit_songs(
            group, concert, 0, ["Комета"], private=True
        )
        await queue_publication(uow.store, world.member, group, concert, 1)
    async with Bot("123456:TEST", session=FakeTelegram()) as bot:
        assert await deliver_one_outbox(bot, engine)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_shared", query)
        base = (await get(client, 1, "performance", **query))["setlist"]
        (proposal,) = (await get(client, 1, "shared_requests", **query))["proposals"]
        declined = await action(
            client, engine, 1, admin, "decline_proposal", base, variant=proposal["id"]
        )
        assert declined["proposal_decision"] == "declined"
        assert not (await get(client, 1, "shared_requests", **query))["proposals"]
        (item,) = (await get(client, 1, "shared_variants", **query))["variants"]
        assert item["declined"] and not item["applied"]
        failed = await action(
            client, engine, 1, admin, "apply", base, variant=proposal["id"], success=False
        )
        assert failed["error"] == "already_done"


async def test_confirmed_publication_from_admin_is_visible_after_delivery_and_can_be_applied(
    engine, world
):
    await setup_workspace(engine, world)
    group, concert = world.group.id, world.concert.id
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(group, concert, 0, ["Комета"])
        await lists.approve(group, concert, 1)
        await lists.edit_songs(group, concert, 0, ["Комета", "Комета"], private=True, replace=True)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        band = await get(client, 2, "performances", band=str(group))
        assert band["performances"][0]["approved"]
        editor = await post(client, 1, "open_draft", query)
        state = await get(client, 1, "state", **editor)
        assert len(state["entries"]) == 2 and len(state["shared"]["entries"]) == 1
        payload = {
            **editor,
            "entries": state["entries"],
            "version": state["version"],
            "request_id": str(uuid4()),
        }
        confirmation = await post(client, 1, "publish", payload)
        assert confirmation == {"status": "confirm_approved", "approved_version": 2}
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(Submission, concert_id=concert)
        payload["approved_version"] = confirmation["approved_version"]
        queued = await post(client, 1, "publish", payload)
        assert queued["status"] == "pending"
        assert await post(client, 1, "publish", payload) == queued
        assert not (await get(client, 1, "inbox"))["admin"]
        relay = FakeTelegram()
        async with Bot("123456:TEST", session=relay) as bot:
            assert await deliver_one_outbox(bot, engine)
        assert len(relay.sent) == 1
        assert (await get(client, 1, "publication", **editor))["status"] == "delivered"
        (global_item,) = (await get(client, 1, "inbox"))["admin"]
        (local_item,) = (await get(client, 1, "shared_requests", **query))["proposals"]
        assert global_item["id"] == local_item["id"] and global_item["kind"] == "setlist"
        async with PostgresUnitOfWork(engine) as uow:
            await Setlists(uow.store, world.admin).reopen(group, concert, 2)
        (local_item,) = (await get(client, 1, "shared_requests", **query))["proposals"]
        assert not local_item["stale"]
        admin = await post(client, 1, "open_shared", query)
        base = (await get(client, 1, "performance", **query))["setlist"]
        assert (await action(client, engine, 1, admin, "apply", base, variant=local_item["id"]))[
            "proposal_decision"
        ] == "applied"
        assert not (await get(client, 1, "inbox"))["admin"]


async def test_variants_are_sorted_by_publication_time_before_paging(engine, world):
    await setup_workspace(engine, world)
    group, concert = world.group.id, world.concert.id
    authored = datetime(2026, 11, 26, tzinfo=UTC)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(group, concert, 0, ["Комета"], private=True)
        proposals = []
        for index in range(21):
            row = await lists.publish(group, concert)
            row.published_at = authored + timedelta(minutes=index)
            row.applied = index % 2 == 0
            proposals.append(str(row.id))
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        recent = await get(client, 1, "shared_variants", **scope(world))
        older = await get(client, 1, "shared_variants", **scope(world), page=1)
    assert [row["id"] for row in recent["variants"]] == list(reversed(proposals[1:]))
    assert recent["has_next"] and [row["id"] for row in older["variants"]] == proposals[:1]


async def test_shared_save_rechecks_administrator_after_queueing(engine, world):
    await setup_workspace(engine, world)
    query = scope(world)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_draft", query)
        base = await get(client, 1, "draft_shared", **admin)
        payload = {
            **admin,
            "action": "edit",
            "version": base["version"],
            "token": base["token"],
            "request_id": str(uuid4()),
            "entries": [],
            "default_pause_seconds": 0,
        }
        sent = await post(client, 1, "shared_action", payload)
        async with PostgresUnitOfWork(engine) as uow:
            (membership,) = await uow.store.find(
                Membership, group_id=world.group.id, user_id=world.admin.id
            )
            membership.role = "member"
            await uow.store.flush()
        assert not await deliver_request(engine, sent["request"])
        async with PostgresUnitOfWork(engine) as uow:
            state = await Setlists(uow.store, world.member).read(world.group.id, world.concert.id)
            assert state[1] == base["version"]
