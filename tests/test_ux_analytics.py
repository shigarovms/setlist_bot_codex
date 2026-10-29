from collections import deque
from datetime import timedelta
from uuid import uuid4

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text
from test_admin import login, settings

from setlist_bot.admin.app import create_app
from setlist_bot.application.ux import Trace
from setlist_bot.domain.entities import now
from setlist_bot.infrastructure.admin_queries import AdminQueries
from setlist_bot.infrastructure.ux_analytics import UXBuffer, action_name
from setlist_bot.transport.telegram import Processor


async def test_usage_events_exclude_content_ids_and_duplicate_updates(engine, world):
    buffer = UXBuffer()
    processor = Processor(engine, analytics=buffer)
    await processor.process(90100, 1, -100, f"/use_concert {world.concert.id}", private=False)
    await processor.process(
        90101, 1, -100, "/add_new_songs Секретное название  03:20", private=False
    )
    size = len(buffer.pending)
    assert await processor.process(90101, 1, -100, "/start", private=False) is None
    assert len(buffer.pending) == size
    await buffer.flush(engine)
    async with engine.connect() as connection:
        rows = (await connection.execute(text("SELECT * FROM ux_events"))).mappings().all()
    assert rows and all(len(r["actor_key"]) == 64 for r in rows)
    for row in rows:
        data = str(dict(row))
        assert "Секретное название" not in data and str(world.admin.id) not in data
        assert "telegram_id" not in data
    assert action_name("mi_toggle abc 102", True) == "mi_toggle"
    assert action_name("slmenu sensitive-identifier options", True) == "slmenu.options"
    assert action_name("TotallySecretUserText", False) == "text_input"


async def test_analytics_is_bounded_and_retry_does_not_double_daily_counts(engine):
    buffer = UXBuffer(capacity=2)
    trace = Trace(actor=uuid4(), group=uuid4())
    for _ in range(5):
        buffer.add(trace, "cancel", "private_text", 10)
    assert buffer.dropped == 3 and len(buffer.pending) == 2
    old_batch = list(buffer.pending)
    await buffer.flush(engine)
    # Simulate losing the success acknowledgement after PostgreSQL committed.
    buffer.pending = deque(old_batch, maxlen=2)
    await buffer.flush(engine)
    async with engine.connect() as connection:
        assert (await connection.execute(text("SELECT sum(events) FROM ux_daily"))).scalar() == 2
        assert (await connection.execute(text("SELECT count(*) FROM ux_events"))).scalar() == 2


async def test_retention_preserves_daily_totals_and_reports_correlated_funnels(engine):
    buffer = UXBuffer()
    actor, group, performance = uuid4(), uuid4(), uuid4()
    for action, resource in (
        ("group_created", group),
        ("catalog_imported", group),
        ("setlist_created", performance),
        ("draft_saved", performance),
        ("variant_published", performance),
        ("setlist_approved", performance),
        ("pdf_requested", performance),
    ):
        buffer.add(Trace(actor=actor, group=group, resource=resource), action, "group_text", 10)
    await buffer.flush(engine)
    report = await AdminQueries(engine).ux()
    assert report["funnels"]["registered"] == 1
    assert report["funnels"]["imported"] == 1
    assert report["funnels"]["approved"] == 1 and report["funnels"]["exported"] == 1
    assert report["people"]["people"] == 1
    buffer.add(Trace(actor=actor), "cancel", "private_text", 10)
    buffer.pending[0]["created_at"] = now() - timedelta(days=91)
    buffer.next_cleanup = now()
    await buffer.flush(engine)
    async with engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT count(*) FROM ux_events WHERE action='cancel'"))
        ).scalar() == 0
        assert (
            await connection.execute(text("SELECT sum(events) FROM ux_daily WHERE action='cancel'"))
        ).scalar() == 1
    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        assert (await browser.get("/analytics", allow_redirects=False)).status == 303
        await login(browser)
        response = await browser.get("/analytics?days=30")
        body = await response.text()
        assert response.status == 200 and "Переходы по сценариям" in body
        assert "Телеграм" not in body
