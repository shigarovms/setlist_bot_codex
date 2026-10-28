import asyncio
import json
from datetime import timedelta
from html.parser import HTMLParser
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from test_telegram import FakeTelegram

from setlist_bot.admin.app import create_app
from setlist_bot.admin.security import AdminSettings, password_hash, password_matches
from setlist_bot.application.support import Support
from setlist_bot.domain.entities import (
    AdminAudit,
    AdminSession,
    Group,
    Membership,
    Outbox,
    PersonalDraft,
    RuntimeStatus,
    now,
)
from setlist_bot.infrastructure.admin_queries import AdminQueries
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.telemetry import Monitor
from setlist_bot.main import deliver_one_outbox

PASSWORD = "Synthetic-admin-password-2026"
HASH = password_hash(PASSWORD)


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action", ""), "data": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and attrs.get("name"):
            self.current["data"][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def extract(text, action):
    return next(form["data"] for form in Forms(text).forms if form["action"] == action)


def settings(engine):
    return AdminSettings(
        _env_file=None,
        database_url=engine.url.render_as_string(hide_password=False),
        username="admin",
        password_hash=HASH,
        secret="synthetic-admin-key-" + "x" * 32,
    )


@pytest_asyncio.fixture
async def client(engine):
    app = create_app(settings(engine), engine)
    async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as browser:
        yield browser


async def login(client, password=PASSWORD):
    response = await client.get("/login")
    values = extract(await response.text(), "/login")
    values.update(username="admin", password=password)
    return await client.post("/login", data=values, allow_redirects=False)


async def seed_ticket(engine, world, text="Нужна помощь"):
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, world.admin).configure(-900, "shigarovms")
        await Support(uow.store, world.member).submit(text)
        return (await uow.store.find(Outbox, support_kind="request"))[0].id


def test_password_encoding_and_production_guard():
    assert password_matches(PASSWORD, HASH)
    assert not password_matches("wrong", HASH)
    assert ":" in HASH and "$" not in HASH
    with pytest.raises(ValueError):
        password_hash("short")
    config = AdminSettings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@host/setlist",
        password_hash=HASH,
        secret="x" * 32,
    )
    with pytest.raises(ValueError, match="non-sandbox"):
        config.checked_url()


async def test_authentication_cookies_csrf_and_host_guard(client, engine):
    for path in ("/", "/users", "/api/overview", "/audit", "/runtime"):
        response = await client.get(path, allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
    assert (await client.get("/healthz")).status == 200
    assert (await client.get("/login", headers={"Host": "evil.example"})).status == 403
    assert (
        await client.post("/login", data={"username": "admin", "password": PASSWORD})
    ).status == 403
    response = await login(client)
    assert response.status == 303
    cookies = response.headers.getall("Set-Cookie")
    assert any("HttpOnly" in cookie and "SameSite=Strict" in cookie for cookie in cookies)
    page = await client.get("/")
    assert page.status == 200
    assert "frame-ancestors" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-store"
    assert (await client.post("/actions/logout/session", data={})).status == 403
    proof = extract(await page.text(), "/actions/logout/session")
    assert (
        await client.post(
            "/actions/logout/session", data=proof, headers={"Origin": "https://evil.example"}
        )
    ).status == 403
    assert (
        await client.post("/actions/logout/session", data=proof, allow_redirects=False)
    ).status == 303
    assert (await client.get("/", allow_redirects=False)).status == 303


async def test_persistent_login_throttle(client, engine):
    for _ in range(6):
        assert (await login(client, "wrong")).status == 401
    assert (await login(client)).status == 429
    # A new app has the same persistent bucket; a restart does not clear the limit.
    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as other:
        assert (await login(other)).status == 429


async def test_pages_are_escaped_and_private_drafts_are_not_exposed(client, engine, world):
    await seed_ticket(engine, world, "<script>alert(1)</script>")
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(
            PersonalDraft(
                group_id=world.group.id,
                concert_id=world.concert.id,
                owner_id=world.member.id,
                content={"private_marker": "PRIVATE_DRAFT_MUST_NOT_LEAK"},
                version=0,
            )
        )
        group = await uow.store.get(Group, world.group.id)
        group.name = "<img src=x onerror=alert(1)>"
    assert (await login(client)).status == 303
    for path in (
        "/",
        "/users",
        "/groups",
        "/concerts",
        "/support",
        "/queue",
        "/audit",
        "/runtime",
        f"/users/{world.member.id}",
        f"/groups/{world.group.id}",
        f"/groups/{world.group.id}/catalog",
        f"/concerts/{world.concert.id}",
    ):
        response = await client.get(path)
        text = await response.text()
        assert response.status == 200, (path, text)
        assert "PRIVATE_DRAFT_MUST_NOT_LEAK" not in text
        assert "<script>alert(1)</script>" not in text and "<img src=x onerror=" not in text
    assert (await client.get("/users?q=%27%20OR%201%3D1--")).status == 200
    assert (await client.get("/users?page=999999")).status == 400


async def test_support_reply_is_queued_audited_and_idempotent(client, engine, world):
    identifier = await seed_ticket(engine, world)
    await login(client)
    page = await client.get(f"/support/{identifier}")
    path = f"/actions/support_reply/{identifier}"
    values = extract(await page.text(), path)
    values.update(message="Наш ответ", close="yes")
    response = await client.post(path, data=values, allow_redirects=False)
    assert response.status == 303
    response = await client.post(path, data=values, allow_redirects=False)
    assert response.status == 303 and "duplicate" in response.headers["Location"]
    async with PostgresUnitOfWork(engine) as uow:
        replies = await uow.store.find(Outbox, support_kind="reply")
        assert (
            len(replies) == 1
            and replies[0].chat_id == 2
            and replies[0].support_request_id == identifier
        )
        audits = await uow.store.find(AdminAudit, action="support_reply")
        assert len(audits) == 1 and "Наш ответ" not in json.dumps(audits[0].details)
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
        await deliver_one_outbox(bot, engine)
    assert session.sent[-1].chat_id == 2 and "Наш ответ" in session.sent[-1].text
    page = await client.get(f"/support/{identifier}")
    assert "Доставлено" in await page.text()


async def test_csrf_proof_is_bound_to_action_and_target(client, engine, world):
    await login(client)
    page = await client.get(f"/groups/{world.group.id}")
    values = extract(await page.text(), f"/actions/group_reminders/{world.group.id}")
    response = await client.post(f"/actions/group_reminders/{world.other.id}", data=values)
    assert response.status == 403
    assert (await client.post(f"/actions/group_owner/{world.group.id}", data=values)).status == 403
    response = await client.post(
        f"/actions/group_reminders/{world.group.id}", data=values, allow_redirects=False
    )
    assert response.status == 303
    async with PostgresUnitOfWork(engine) as uow:
        assert not (await uow.store.get(Group, world.group.id)).reminders_enabled
        assert (await uow.store.get(Group, world.other.id)).reminders_enabled


async def test_owner_transfer_requires_name_and_fresh_owner(client, engine, world):
    await login(client)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
    page = await client.get(f"/groups/{world.group.id}/owner?member={member.id}")
    values = extract(await page.text(), f"/actions/group_owner/{world.group.id}")
    values["confirmation"] = "wrong"
    assert (await client.post(f"/actions/group_owner/{world.group.id}", data=values)).status == 409
    values["confirmation"] = world.group.name
    assert (
        await client.post(
            f"/actions/group_owner/{world.group.id}", data=values, allow_redirects=False
        )
    ).status == 303
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Group, world.group.id)).owner_id == world.member.id
        assert (await uow.store.get(Membership, member.id)).role == "admin"
        assert len(await uow.store.find(AdminAudit, action="group_owner")) == 1


async def test_queue_failure_is_deferred_and_does_not_starve_next_event(client, engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        failed = Outbox(chat_id=1, text="First")
        healthy = Outbox(chat_id=2, text="Second")
        uow.store.add(failed)
        uow.store.add(healthy)
    from unittest.mock import AsyncMock

    bad_bot = AsyncMock()
    bad_bot.send_message.side_effect = TelegramNetworkError(
        method=SendMessage(chat_id=1, text="First"), message="unavailable"
    )
    with pytest.raises(TelegramNetworkError):
        await deliver_one_outbox(bad_bot, engine)
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await deliver_one_outbox(bot, engine)
    assert session.sent[-1].chat_id == 2
    async with PostgresUnitOfWork(engine) as uow:
        stored = await uow.store.get(Outbox, failed.id)
        assert (
            not stored.sent
            and stored.attempts == 1
            and stored.last_error == "TelegramNetworkError"
            and stored.next_attempt_at > now()
        )
    await login(client)
    page = await client.get("/queue?state=failed")
    values = extract(await page.text(), f"/actions/queue_discard/{failed.id}")
    values.pop("confirmed", None)
    assert (await client.post(f"/actions/queue_discard/{failed.id}", data=values)).status == 409
    values["confirmed"] = "yes"
    assert (
        await client.post(f"/actions/queue_discard/{failed.id}", data=values, allow_redirects=False)
    ).status == 303
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Outbox, failed.id)).discarded_at


async def test_pagination_bounds_and_1000_users(client, engine):
    from setlist_bot.domain.entities import User

    async with PostgresUnitOfWork(engine) as uow:
        for index in range(1005):
            uow.store.add(User(telegram_id=100000 + index, display_name=f"Test user {index}"))
    rows = await AdminQueries(engine).listing("users")
    assert len(rows) == 26
    await login(client)
    results = await asyncio.gather(*(client.get("/api/overview") for _ in range(10)))
    assert all(response.status == 200 for response in results)
    payloads = await asyncio.gather(*(response.json() for response in results))
    assert all(payload["users"] == 1005 for payload in payloads)
    response = await client.get("/users?page=39")
    assert response.status == 200 and "Страница 40" in await response.text()


async def test_runtime_signals_and_expired_sessions(client, engine):
    monitor = Monitor()
    monitor.set("outbox", "retrying", error="TelegramNetworkError")
    await monitor.flush(engine)
    async with PostgresUnitOfWork(engine) as uow:
        row = (await uow.store.find(RuntimeStatus))[0]
        assert row.errors == 1 and row.last_activity_at
    await login(client)
    response = await client.get("/runtime")
    assert response.status == 200 and "TelegramNetworkError" in await response.text()
    async with PostgresUnitOfWork(engine) as uow:
        for session in await uow.store.find(AdminSession):
            session.expires_at = now() - timedelta(seconds=1)
    assert (await client.get("/runtime", allow_redirects=False)).status == 303


async def test_unicode_bad_login_and_bad_form_proof_are_rejected(client, engine, world):
    page = await client.get("/login")
    values = extract(await page.text(), "/login")
    values.update(username="Администратор", password=PASSWORD)
    assert (await client.post("/login", data=values)).status == 401
    await login(client)
    page = await client.get(f"/groups/{world.group.id}")
    values = extract(await page.text(), f"/actions/group_reminders/{world.group.id}")
    values["proof"] = "я" * 64
    assert (
        await client.post(f"/actions/group_reminders/{world.group.id}", data=values)
    ).status == 403


async def test_revoke_all_sessions_invalidates_other_browser(client, engine):
    await login(client)
    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as other:
        await login(other)
        page = await client.get("/runtime")
        values = extract(await page.text(), "/actions/revoke_sessions/session")
        values["confirmed"] = "yes"
        assert (
            await client.post(
                "/actions/revoke_sessions/session", data=values, allow_redirects=False
            )
        ).status == 303
        assert (await other.get("/", allow_redirects=False)).status == 303


async def test_restore_group_and_concert_uses_existing_lifecycle_rules(client, engine, world):
    from setlist_bot.application.trash import Trash

    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        await trash.delete_concert(
            world.group.id,
            world.concert.id,
            await trash.concert_fingerprint(world.group.id, world.concert.id),
        )
        await trash.delete_group(world.group.id, await trash.group_fingerprint(world.group.id))
    await login(client)
    page = await client.get(f"/groups/{world.group.id}")
    values = extract(await page.text(), f"/actions/restore_group/{world.group.id}")
    values["confirmed"] = "yes"
    assert (
        await client.post(
            f"/actions/restore_group/{world.group.id}", data=values, allow_redirects=False
        )
    ).status == 303
    page = await client.get(f"/concerts/{world.concert.id}")
    values = extract(await page.text(), f"/actions/restore_concert/{world.concert.id}")
    values["confirmed"] = "yes"
    assert (
        await client.post(
            f"/actions/restore_concert/{world.concert.id}", data=values, allow_redirects=False
        )
    ).status == 303
    async with PostgresUnitOfWork(engine) as uow:
        from setlist_bot.domain.entities import Concert

        assert (await uow.store.get(Group, world.group.id)).deleted_at is None
        assert (await uow.store.get(Concert, world.concert.id)).deleted_at is None


async def test_legacy_timestamps_are_unknown_after_migration(database_url, engine):
    from sqlalchemy import text
    from test_migrations import alembic

    identifier = uuid4()
    alembic(database_url, "downgrade", "f8129a4b6c30")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO outbox(id,chat_id,text,sent,buttons,support_kind,"
                "monospace,monospace_offset) "
                "VALUES (:id,123,'old event',true,'[]'::jsonb,'',false,0)"
            ),
            {"id": identifier},
        )
    alembic(database_url, "upgrade", "head")
    async with PostgresUnitOfWork(engine) as uow:
        event = await uow.store.get(Outbox, identifier)
        assert event.created_at is None and event.delivered_at is None and event.attempts == 0


def test_host_report_is_sanitized_and_staleness_is_visible():
    from setlist_bot.admin.views import host_panel

    report = {
        "collected_at": (now() - timedelta(hours=1)).isoformat(),
        "disk_free_bytes": 10**9,
        "disk_total_bytes": 2 * 10**9,
        "last_backup": "<script>alert(1)</script>",
        "backup_timer": "inactive",
    }
    output = host_panel(report)
    assert "Отчёт устарел" in output and "<script>" not in output
    assert "неполон" in host_panel({"collected_at": "broken"})


def test_polling_monitor_never_copies_log_payload():
    import logging

    from setlist_bot.infrastructure.telemetry import PollingStatusHandler

    monitor = Monitor()
    PollingStatusHandler(monitor).emit(
        logging.LogRecord("aiogram.dispatcher", logging.ERROR, "", 0, "sensitive payload", (), None)
    )
    assert monitor.components["telegram"]["last_error"] == "TelegramPollingError"
    assert "sensitive" not in repr(monitor.components)


def test_delivery_labels_never_claim_a_failed_or_discarded_reply_was_delivered():
    from setlist_bot.admin.views import delivery_status

    assert delivery_status({"sent": True, "last_error": "TelegramForbiddenError"})[1] == "bad"
    assert "Исключено" in delivery_status({"sent": True, "discarded_at": now()})[0]
    assert delivery_status({"sent": True})[0] == "Обработано"
    assert delivery_status({"sent": True, "delivered_at": now()})[0] == "Доставлено"
