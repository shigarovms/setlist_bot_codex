"""RU: История и средняя — только личный черновик. EN: No shared writes or draft leaks."""

from uuid import uuid4

import pytest
from test_miniapp import session, song
from test_oct29 import make_past

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Concert, PersonalDraft, Revision
from setlist_bot.domain.rules import AppError, totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def test_average_pause_is_atomic_private_idempotent_and_keeps_explicit_pauses(engine, world):
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        before = await service.lists.read(world.group.id, world.concert.id)
        before_revisions = len(await uow.store.find(Revision))
        values = [song(world), song(world), {"kind": "pause", "duration_seconds": 180}, song(world)]
        request = str(uuid4())
        saved = await service.save(identifier, initial["version"], values, request, 30)
        assert saved == await service.save(identifier, initial["version"], values, request, 30)
        assert saved["version"] == initial["version"] + 1
        assert saved["default_pause_seconds"] == 30
        snapshot, _, _ = await service.lists.read(world.group.id, world.concert.id, private=True)
        assert totals(snapshot)[2] == 218 * 3 + 180 + 30
        assert await service.lists.read(world.group.id, world.concert.id) == before
        assert len(await uow.store.find(Revision)) == before_revisions
        with pytest.raises(AppError, match="input"):
            await service.save(identifier, initial["version"], values, request, 60)
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        zero = await service.save(identifier, saved["version"], values, str(uuid4()), 0)
        assert zero["default_pause_seconds"] == 0
        with pytest.raises(AppError, match="conflict"):
            await service.save(identifier, saved["version"], values, str(uuid4()), 10)


@pytest.mark.parametrize("gap", [-1, True, 1.5, "30", 86401])
async def test_average_pause_rejects_invalid_input_without_saving(engine, world, gap):
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        with pytest.raises(AppError, match="pause_duration"):
            await service.save(identifier, initial["version"], [song(world)], str(uuid4()), gap)
        assert await service.state(identifier) == initial


async def test_empty_history_recommends_type_then_duration_then_recency(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        # Matching type beats even an exact duration of a different type.
        best, _ = await make_past(uow, world, 10, 900, "Сольник")
        await make_past(uow, world, 20, 900, "Сольник")
        await make_past(uow, world, 2, 1000, "Сольник")
        await make_past(uow, world, 1, 600, "Фест")
        await make_past(uow, world, 3, 600, "Сольник", approved=False)
        await make_past(uow, world, 4, 600, "Сольник", group=world.other.id)
        target = await uow.store.get(Concert, world.concert.id)
        target.performance_type, target.performance_duration_seconds = "Сольник", 600
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        # Autosave must not remove the smart first entry for an initially empty list.
        await service.save(identifier, initial["version"], [song(world)], str(uuid4()))
        history = [await service.history(identifier, n) for n in range(4)]
        assert history[0]["kind"] == "recommendation" and history[0]["seconds"] == 900
        assert str(best.date.day).zfill(2) in history[0]["label"]
        assert [r["seconds"] for r in history[1:]] == [600, 1000, 900]
        assert history[0]["count"] == 4
        assert all(r["kind"] == "performance" for r in history[1:])
        with pytest.raises(AppError, match="miniapp_no_history"):
            await service.history(identifier, 4)


async def test_current_public_versions_precede_past_and_exclude_private_drafts(engine, world):
    from test_miniapp_group import template

    async with PostgresUnitOfWork(engine) as uow:
        await make_past(uow, world, 1, 600)
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"], replace=True)
        await lists.edit_songs(
            world.group.id, world.concert.id, 1, ["Комета", "Комета"], replace=True
        )
        uow.store.add(
            PersonalDraft(
                group_id=world.group.id,
                concert_id=world.concert.id,
                owner_id=world.member.id,
                content={"default_pause_seconds": 123, "blocks": []},
            )
        )
    launch = await template(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        identifier = (await MiniDrafts(uow.store, world.admin).open_template(launch)).id
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        rows = [await service.history(identifier, n) for n in range(3)]
        assert [r["kind"] for r in rows] == ["version", "version", "performance"]
        assert [r["label"] for r in rows[:2]] == ["Версия 2", "Версия 1"]
        assert [len(r["entries"]) for r in rows[:2]] == [2, 1]
        # New shared versions during an open editor do not reshuffle its history.
        await service.lists.edit_songs(
            world.group.id, world.concert.id, 2, ["Комета"], replace=True
        )
        assert await service.history(identifier, 0) == rows[0]
        reopened = await service.open_template(launch)
        assert reopened.id == identifier
        assert (await service.history(identifier, 0))["label"] == "Версия 3"


async def test_primary_buttons_open_editor_directly_and_legacy_menu_can_be_restored(engine, world):
    processor = Processor(
        engine, bot_username="botToPract696_bot", miniapp_public_base_url="https://example.org"
    )
    shared = await processor.process(1, 1, -100, f"/use_concert {world.concert.id}", private=False)
    assert shared.buttons[0][0] == "Предложить свой вариант"
    assert "?startapp=sl_" in shared.buttons[0][1]
    await processor.process(2, 1, 1, f"/use_group {world.group.id}", private=True)
    await processor.process(3, 1, 1, f"/use_concert {world.concert.id}", private=True)
    draft = await processor.process(4, 1, 1, "/suggestion Комета", private=True)
    assert draft.buttons[0][0] == "Изменить" and "?startapp=sl_" in draft.buttons[0][1]
    prompt = await processor.process(5, 1, 1, "/suggestion", private=True)
    assert len(prompt.buttons) == 1 and "?startapp=sl_" in prompt.buttons[0][1]
    processor.setlist_legacy_input_enabled = True
    legacy = await processor.process(6, 1, 1, "/suggestion", private=True)
    assert any(a.startswith("pick_text ") for _, a in legacy.buttons)
    assert any(a.startswith("pick_start ") for _, a in legacy.buttons)


async def test_direct_launch_keeps_existing_telegram_permission_request(engine, world):
    from aiohttp.test_utils import TestClient, TestServer
    from test_miniapp import settings, signed
    from test_miniapp_group import template

    from setlist_bot.domain.entities import DeploymentIdentity, Group
    from setlist_bot.miniapp.app import create_app

    target = await template(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        (await uow.store.get(Group, world.group.id)).admins_only_suggestions = True
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        response = await client.get(
            "/miniapp/api/launch",
            headers={
                "Authorization": "tma " + signed(actor=2, start_param="sl_" + target.hex),
            },
        )
        data = await response.json()
        assert response.status == 403 and data["error"] == "admin_suggestions_only"
        assert data["action_url"].endswith("?start=draft_" + target.hex)
    processor = Processor(
        engine, bot_username="botToPract696_bot", miniapp_public_base_url="https://example.org"
    )
    denial = await processor.process(1, 2, 2, "/start draft_" + target.hex, private=True)
    assert any("Запросить" in text for text, _ in denial.buttons)
