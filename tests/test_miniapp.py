import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import secrets
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import (
    Concert,
    Confirmation,
    DeploymentIdentity,
    Group,
    Membership,
    PersonalDraft,
    Revision,
    Submission,
    now,
)
from setlist_bot.domain.miniapp_auth import verify_init_data
from setlist_bot.domain.rules import AppError, totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import ENGINE, create_app
from setlist_bot.miniapp.settings import MiniAppSettings
from setlist_bot.transport.telegram import Processor, keyboards

KEY = hmac.new(b"WebAppData", b"test-token-not-a-real-bot", hashlib.sha256).digest()


def signed(actor=1, **fields):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": actor}), **fields}
    check = "\n".join(f"{name}={value}" for name, value in sorted(data.items()))
    data["hash"] = hmac.new(KEY, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def settings(engine):
    return MiniAppSettings(
        database_url=engine.url.render_as_string(hide_password=False),
        expected_bot_id=1110437563,
        environment="sandbox",
        bot_username="botToPract696_bot",
        auth_key=base64.urlsafe_b64encode(KEY).decode(),
        public_base_url="https://example.org",
    )


async def session(engine, world, member=False):
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.member if member else world.admin)
        row = await service.create(world.group.id, world.concert.id)
        state = await service.state(row.id)
    return row.id, state


def song(world, **fields):
    return {
        "kind": "song",
        "song_id": str(world.song.id),
        "version": world.song.version,
        "pause_before": None,
        **fields,
    }


def test_signed_telegram_identity_rejects_forgery_age_duplicate_and_group():
    assert verify_init_data(signed(), KEY) == 1
    for raw in (
        signed().replace("hash=", "hash=f"),
        signed(auth_date="1"),
        signed(auth_date=str(int(time.time()) + 60)),
        signed() + "&user=x",
        signed(user='{"id":true}'),
        signed(user='{"id":1,"is_bot":true}'),
        signed(chat_type="supergroup"),
        "",
        "user=broken",
        signed(user="[]"),
    ):
        with pytest.raises(AppError):
            verify_init_data(raw, KEY)
    with pytest.raises(AppError):
        verify_init_data(signed(), b"wrong bot")


async def test_save_uses_existing_private_draft_duplicates_pauses_and_idempotency(engine, world):
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        revisions = await uow.store.find(Revision)
        before = await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
        service = MiniDrafts(uow.store, world.admin)
        values = [
            song(world, pause_before=7),
            {"kind": "pause", "duration_seconds": 180},
            song(world),
            song(world, pause_before=0),
        ]
        request = str(uuid4())
        saved = await service.save(identifier, initial["version"], values, request)
        again = await service.save(identifier, initial["version"], values, request)
        assert saved == again and saved["version"] == initial["version"] + 1
        # Legacy lead-ins become explicit pauses through the existing normalizer.
        assert [entry["kind"] for entry in saved["entries"]] == [
            "pause",
            "song",
            "pause",
            "song",
            "song",
        ]
        private = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id, private=True
        )
        assert totals(private[0])[2] == 3 * 218 + 7 + 180
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id) == before
        )
        assert len(await uow.store.find(Revision)) == len(revisions)
        assert not await uow.store.find(Submission)
        assert len(await uow.store.find(PersonalDraft)) == 1
        assert await service.pause(identifier, "3") == 180
        assert await service.pause(identifier, "00:00:03") == 3
        with pytest.raises(AppError, match="conflict"):
            await service.save(identifier, initial["version"], values, str(uuid4()))
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        empty = await service.save(identifier, saved["version"], [], str(uuid4()))
        assert empty["entries"] == []


@pytest.mark.parametrize("same_request", [True, False])
async def test_simultaneous_saves_are_serialized_and_do_not_overwrite(engine, world, same_request):
    identifier, initial = await session(engine, world)
    request = str(uuid4())

    async def save(request_id):
        try:
            async with PostgresUnitOfWork(engine) as uow:
                return await MiniDrafts(uow.store, world.admin).save(
                    identifier, initial["version"], [song(world)], request_id
                )
        except AppError as exc:
            return exc.code

    results = await asyncio.gather(save(request), save(request if same_request else str(uuid4())))
    if same_request:
        assert results[0] == results[1]
    else:
        assert results.count("conflict") == 1
    async with PostgresUnitOfWork(engine) as uow:
        current = await MiniDrafts(uow.store, world.admin).state(identifier)
        assert current["version"] == initial["version"] + 1


@pytest.mark.parametrize("failure", ["owner", "role", "restricted", "epoch", "expired"])
async def test_every_operation_rechecks_owner_roles_lifecycle_and_expiry(engine, world, failure):
    identifier, _ = await session(engine, world, member=True)
    async with PostgresUnitOfWork(engine) as uow:
        if failure == "role":
            row = (await uow.store.find(Membership, user_id=world.member.id))[0]
            row.status = "pending"
        elif failure == "restricted":
            (await uow.store.get(Group, world.group.id)).admins_only_suggestions = True
        elif failure == "epoch":
            (await uow.store.get(Concert, world.concert.id)).lifecycle_version += 1
        elif failure == "expired":
            (await uow.store.get(Confirmation, identifier)).expires_at = now() - timedelta(
                seconds=1
            )
    async with PostgresUnitOfWork(engine) as uow:
        actor = world.admin if failure == "owner" else world.member
        with pytest.raises(AppError):
            await MiniDrafts(uow.store, actor).state(identifier)


@pytest.mark.parametrize("value", ["foreign", "version", "archived", "pause", "lead", "shape"])
async def test_catalog_and_input_validation_is_atomic(engine, world, value):
    from setlist_bot.domain.entities import Song

    identifier, initial = await session(engine, world)
    values = [song(world)]
    if value == "foreign":
        values[0]["song_id"] = str(world.other_song.id)
    elif value == "version":
        values[0]["version"] += 1
    elif value == "pause":
        values = [{"kind": "pause", "duration_seconds": True}]
    elif value == "lead":
        values[0]["pause_before"] = -1
    elif value == "shape":
        values = [None]
    elif value == "archived":
        async with PostgresUnitOfWork(engine) as uow:
            (await uow.store.get(Song, world.song.id)).archived = True
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        with pytest.raises(AppError):
            await service.save(identifier, initial["version"], values, str(uuid4()))
    async with PostgresUnitOfWork(engine) as uow:
        assert (await MiniDrafts(uow.store, world.admin).state(identifier))["entries"] == []


async def test_history_uses_latest_approval_per_past_performance_and_never_mutates(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        for when, titles in [
            (date(2026, 1, 1), ["Комета"]),
            (date(2026, 2, 1), ["Комета", "Комета"]),
        ]:
            event = await lists.create(world.group.id, when)
            _, version, _ = await lists.read(world.group.id, event.id)
            await lists.edit_songs(world.group.id, event.id, version, titles)
            _, version, _ = await lists.read(world.group.id, event.id)
            await lists.approve(world.group.id, event.id, version)
        # A later unapproved concert must not hide the two approved history entries.
        await lists.create(world.group.id, date(2026, 3, 1))
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        recent, older = await service.history(identifier, 0), await service.history(identifier, 1)
        assert recent["count"] == 2 and len(recent["entries"]) == 2
        assert len(older["entries"]) == 1
        assert await service.state(identifier) == initial
        with pytest.raises(AppError, match="miniapp_no_history"):
            await service.history(identifier, 2)


async def test_http_origin_identity_conflict_headers_and_real_restricted_database_role(
    engine, world
):
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    spec = importlib.util.spec_from_file_location(
        "miniapp_setup", Path(__file__).parents[1] / "scripts/setup-sandbox-miniapp.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    role, password = "mini_test_" + uuid4().hex[:12], secrets.token_hex(32)
    setup.ROLE = role
    assert engine.url.database.endswith("_test")
    grants = setup.grants().replace(
        "ON DATABASE setlist_sandbox", f"ON DATABASE {engine.url.database}"
    )
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        for statement in grants.split(";"):
            if statement.strip():
                await connection.execute(text(statement))
    config = settings(engine).model_copy(
        update={
            "database_url": engine.url.set(username=role, password=password).render_as_string(
                hide_password=False
            )
        }
    )
    app = create_app(config)
    headers = {"Authorization": "tma " + signed(), "Origin": config.public_base_url}
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/miniapp/")
            assert response.status == 200 and "telegram-web-app.js" in await response.text()
            assert response.headers["Cache-Control"] == "no-store"
            assert "frame-ancestors" in response.headers["Content-Security-Policy"]
            assert (await client.get("/miniapp/.env")).status == 404
            assert (await client.get("/healthz")).status == 200
            query = {"session": str(identifier)}
            assert (await client.get("/miniapp/api/state", params=query)).status == 401
            assert (
                await client.get(
                    "/miniapp/api/state",
                    params=query,
                    headers={"Authorization": "tma " + signed(actor=2)},
                )
            ).status == 401
            response = await client.get("/miniapp/api/state", params=query, headers=headers)
            assert response.status == 200
            assert (await response.json())["version"] == initial["version"]
            payload = {
                **query,
                "version": initial["version"],
                "entries": [song(world)],
                "request_id": str(uuid4()),
            }
            assert (
                await client.post(
                    "/miniapp/api/save",
                    json=payload,
                    headers={**headers, "Origin": "https://evil.example"},
                )
            ).status == 401
            response = await client.post("/miniapp/api/save", json=payload, headers=headers)
            assert response.status == 200, await response.text()
            assert (
                await client.post("/miniapp/api/save", json=payload, headers=headers)
            ).status == 200
            payload["request_id"] = str(uuid4())
            response = await client.post("/miniapp/api/save", json=payload, headers=headers)
            assert response.status == 409 and "latest" in await response.json()
            payload["version"] = (await response.json())["latest"]["version"]
            payload["request_id"] = str(uuid4())
            published = await client.post("/miniapp/api/publish", json=payload, headers=headers)
            assert published.status == 200, await published.text()
            assert (await published.json())["status"] == "pending"
            status = await client.get("/miniapp/api/publication", params=query, headers=headers)
            assert status.status == 200 and (await status.json())["status"] == "pending"
            from test_miniapp_group import template

            launch_id = await template(engine, world)
            launched = await client.get(
                "/miniapp/api/launch",
                headers={
                    **headers,
                    "Authorization": "tma "
                    + signed(actor=2, chat_type="supergroup", start_param="sl_" + launch_id.hex),
                },
            )
            assert launched.status == 200, await launched.text()
            for statement in (
                "SELECT text FROM outbox",
                "SELECT payload FROM outbox",
                "SELECT * FROM support_messages",
                "SELECT * FROM admin_sessions",
                "SELECT token_ciphertext FROM calendar_subscriptions",
                "UPDATE setlists SET approved=true",
                "UPDATE songs SET title='bad'",
                "UPDATE memberships SET role='admin'",
                "DELETE FROM personal_drafts",
            ):
                async with app[ENGINE].connect() as connection:
                    with pytest.raises(DBAPIError):
                        await connection.execute(text(statement))
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP OWNED BY {role}"))
            await connection.execute(text(f"DROP ROLE {role}"))


async def test_third_input_method_is_a_real_private_webapp_button(engine, world):
    processor = Processor(engine, miniapp_public_base_url="https://example.org")
    await processor.process(0, 1, 1, f"/use_group {world.group.id}", private=True)
    await processor.process(1, 1, 1, f"/use_concert {world.concert.id}", private=True)
    response = await processor.process(2, 1, 1, "/suggestion", private=True)
    assert len(response.web_apps) == 1
    markup = [value async for value in keyboards(response.buttons, web_apps=response.web_apps)][0]
    web_buttons = [b for row in markup.inline_keyboard for b in row if b.web_app]
    assert len(web_buttons) == 1 and "/miniapp/#session=" in web_buttons[0].web_app.url
    await processor.process(3, 1, -100, f"/use_concert {world.concert.id}", private=False)
    public = await processor.process(4, 1, -100, "/suggestion", private=False)
    assert not public.web_apps
