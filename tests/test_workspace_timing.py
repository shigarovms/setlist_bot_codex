from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from conftest import invite_fixture_member
from test_catalog_articles import ArticleTelegram
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_telegram import FakeTelegram
from test_workspace_performances import form, step
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.setlists import Setlists
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.workspace_timing import TIMING_KIND, schedule_timing, timing_rows
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import Concert, DeploymentIdentity, GigInvitation, GigSlot, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.performance_timing import timing_response

ROWS = [
    {"start": "17:00", "end": "17:45", "label": "Саундчек", "day": 0},
    {"start": "00:15", "end": "", "label": "Окончание", "day": 1},
]


async def act(client, engine, actor, session, action, state, performance, **fields):
    payload = {
        **session,
        "action": action,
        "performance": str(performance),
        "version": state["version"],
        "epoch": state["epoch"],
        "request_id": str(uuid4()),
        **fields,
    }
    queued = await post(client, actor, "timing_action", payload)
    assert await deliver_request(engine, queued["request"])
    result = await get(client, actor, "catalog_status", **session, request=queued["request"])
    assert await post(client, actor, "timing_action", payload) == result
    return result


def evening(event, hour):
    local = datetime.combine(
        event.date - timedelta(days=1), datetime.min.time(), ZoneInfo("Europe/Berlin")
    )
    return (local + timedelta(hours=hour)).astimezone(UTC)


async def test_timing_permissions_manual_notification_and_scheduled_deduplication(engine, world):
    concert = world.concert
    group = world.group.id
    scope = {"band": str(group), "performance": str(concert.id)}
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        await invite_fixture_member(uow.store, concert, world.stranger)
        await invite_fixture_member(uow.store, concert, world.stranger)
        await invite_fixture_member(uow.store, concert, world.member, status="pending")
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        empty = await get(client, 1, "timing", **scope)
        assert not empty["rows"] and empty["admin"] and empty["editor"]
        assert not (await get(client, 3, "timing", **scope))["editor"]
        await get(client, 2, "timing", 403, **scope)
        await get(client, 1, "timing", 403, **{**scope, "band": str(world.other.id)})
        admin = await post(client, 1, "open_repertoire", {"band": str(group)})
        settings_payload = {
            **admin,
            "request_id": str(uuid4()),
            "expected": {"timing_coordinator_id": ""},
            "changes": {"timing_coordinator_id": str(world.member.id)},
        }
        queued = await post(client, 1, "band_settings_save", settings_payload)
        assert await deliver_request(engine, queued["request"])
        assert (
            await get(client, 2, "timing", band=str(group), performance=str(world.concert2.id))
        )["editor"]
        await act(
            client,
            engine,
            1,
            admin,
            "save",
            empty,
            concert.id,
            rows=[{key: row[key] for key in ("start", "end", "label")} for row in ROWS],
            zone="Europe/Berlin",
            preliminary=True,
        )
        state = await get(client, 2, "timing", **scope)
        assert state["editor"] and not state["admin"]
        assert state["rows"] == ROWS and state["preliminary"]
        guest = await get(client, 3, "timing", **scope)
        assert guest["rows"] == ROWS and "members" not in guest
        member = await post(client, 2, "open_repertoire", {"band": str(group)})
        saved = await act(client, engine, 2, member, "notify", state, concert.id)
        assert saved["queued"] == 1
        for hour in (17, 18, 18, 19, 19):
            async with PostgresUnitOfWork(engine) as uow:
                await schedule_timing(uow.store, group, concert.id, evening(concert, hour))
        async with PostgresUnitOfWork(engine) as uow:
            messages = await uow.store.find(Outbox, concert_id=concert.id, support_kind=TIMING_KIND)
            assert {(m.payload["kind"], m.chat_id) for m in messages} == {
                ("warning", world.member.telegram_id),
                ("manual", world.stranger.telegram_id),
            }
            assert all(m.payload["preliminary"] for m in messages)


async def test_automatic_send_uses_latest_version_and_empty_timetable_only_warns(engine, world):
    group = world.group.id
    concert = world.concert
    scope = {"band": str(group), "performance": str(concert.id)}
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        event = await uow.store.get(Concert, concert.id)
        event.timing = {"zone": "Europe/Berlin"}
        await invite_fixture_member(uow.store, event, world.stranger)
        await schedule_timing(uow.store, group, concert.id, evening(concert, 18))
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": str(group)})
        state = await get(client, 1, "timing", **scope)
        await act(
            client,
            engine,
            1,
            admin,
            "save",
            state,
            concert.id,
            rows=ROWS[:1],
            zone="Europe/Berlin",
            preliminary=True,
        )
        state = await get(client, 1, "timing", **scope)
        await act(client, engine, 1, admin, "notify", state, concert.id)
        await act(
            client,
            engine,
            1,
            admin,
            "save",
            state,
            concert.id,
            rows=ROWS,
            zone="Europe/Berlin",
            preliminary=False,
        )
    async with PostgresUnitOfWork(engine) as uow:
        await schedule_timing(uow.store, group, concert.id, evening(concert, 19))
        messages = await uow.store.find(Outbox, concert_id=concert.id, support_kind=TIMING_KIND)
        (automatic,) = [m for m in messages if m.payload["kind"] == "automatic"]
        assert automatic.payload["rows"] == ROWS and not automatic.payload["preliminary"]
        for sent in (m for m in messages if m.payload["kind"] in {"manual", "automatic"}):
            rendered = timing_response(world.group, concert, sent, "ru", "")
            assert "Europe/Berlin" not in rendered.text and "Берлин" not in rendered.text
            assert "17:00" in rendered.text
        assert automatic.chat_id == world.stranger.telegram_id
        empty = await uow.store.get(Concert, world.concert2.id)
        empty.timing = {"zone": "Europe/Berlin"}
        for hour in (18, 19):
            await schedule_timing(uow.store, group, empty.id, evening(empty, hour))
        empty_messages = await uow.store.find(Outbox, concert_id=empty.id, support_kind=TIMING_KIND)
        assert [m.payload["kind"] for m in empty_messages] == ["warning"]


async def test_performance_template_copies_schedule_and_lineup_without_inviting(engine, world):
    await setup_workspace(engine, world)
    group = world.group.id
    band = {"band": str(group)}
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        past = await lists.create(group, today() - timedelta(days=1), "Праздник", city="Тула")
        past.timing = {"rows": ROWS, "zone": "Europe/Moscow", "version": 1}
        slot = await Staffing(uow.store, world.admin).role(group, "Вокал")
        await Staffing(uow.store, world.admin).add_slot(past.id, slot.id)
        await lists.edit_songs(group, past.id, 0, ["Комета"])
        for offset in range(2, 12):
            await lists.create(group, today() - timedelta(days=offset), f"Дата {offset}")
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        history = await get(client, 1, "performance_history", **band)
        assert history["has_next"] and len(history["performances"]) == 10
        assert history["performances"][0]["id"] == str(past.id)
        older = await get(client, 1, "performance_history", **band, page=1)
        assert len(older["performances"]) == 1 and not older["has_next"]
        admin = await post(client, 1, "open_repertoire", band)
        preview = await step(
            client,
            engine,
            admin,
            "create",
            lineup="none",
            source=str(past.id),
            copy_timing=True,
            copy_lineup=True,
            copy_setlist=True,
            **form(city="Тула"),
        )
        created = await step(client, engine, admin, "confirm", preview=preview["request"])
        history = await get(client, 1, "performance_history", **band)
        assert [row["id"] for row in history["recommended"]] == [str(past.id)]
    async with PostgresUnitOfWork(engine) as uow:
        (event,) = await uow.store.find(Concert, template_source_id=past.id)
        assert str(event.id) == created["performance"] and event.id != past.id
        assert event.timing["rows"] == ROWS and event.timing["preliminary"]
        assert len(await uow.store.find(GigSlot, concert_id=event.id)) == 1
        assert not await uow.store.find(GigInvitation, concert_id=event.id)
        result, _, approved = await Setlists(uow.store, world.admin).read(group, event.id)
        assert result["blocks"][0]["items"][0]["title"] == "Комета" and not approved


async def test_timing_warning_buttons_and_delivery_recheck(engine, world, monkeypatch):
    monkeypatch.setenv("MINIAPP_PUBLIC_BASE_URL", "https://example.org")
    group = world.group.id
    concert = world.concert
    async with PostgresUnitOfWork(engine) as uow:
        event = await uow.store.get(Concert, concert.id)
        event.timing = {"zone": "Europe/Berlin", "version": 1, "rows": ROWS}
        guest = await invite_fixture_member(uow.store, event, world.stranger)
        await schedule_timing(uow.store, group, concert.id, evening(concert, 18))
    telegram = FakeTelegram()
    async with Bot("123456:TEST", session=telegram) as bot:
        assert await deliver_one_outbox(bot, engine)
        (warning,) = telegram.sent
        assert warning.chat_id == world.admin.telegram_id and "19:00" in warning.text
        open_link, send, mark = [row[0] for row in warning.reply_markup.inline_keyboard]
        assert open_link.web_app and "view=timing" in open_link.web_app.url
        assert send.callback_data.startswith("pt_send ")
        async with PostgresUnitOfWork(engine) as uow:
            c = Controller(uow.store, world.admin, world.admin.telegram_id, private=True)
            assert "предварительный" in (await c.execute(mark.callback_data)).text
            await schedule_timing(uow.store, group, concert.id, evening(concert, 19))
        async with PostgresUnitOfWork(engine) as uow:
            invite = await uow.store.get(GigInvitation, guest.id)
            invite.status = "replaced"
        assert not await deliver_one_outbox(bot, engine)
        assert len(telegram.sent) == 1


async def test_timing_uses_city_until_overridden_and_sends_article_with_day_sections(engine, world):
    await setup_workspace(engine, world)
    group, event_id = world.group.id, world.concert.id
    scope = {"band": str(group), "performance": str(event_id)}
    async with PostgresUnitOfWork(engine) as uow:
        event = await uow.store.get(Concert, event_id)
        event.city = "Екатеринбург"
        await invite_fixture_member(uow.store, event, world.stranger)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        state = await get(client, 1, "timing", **scope)
        assert state["zone"] == "Asia/Yekaterinburg" and state["manual_zone"] == ""
        assert any(choice["label"] == "Екатеринбург" for choice in state["zones"])
        admin = await post(client, 1, "open_repertoire", {"band": str(group)})
        await act(
            client, engine, 1, admin, "save", state, event_id, rows=ROWS, zone="", preliminary=False
        )
        async with PostgresUnitOfWork(engine) as uow:
            (await uow.store.get(Concert, event_id)).city = "Владивосток"
        state = await get(client, 1, "timing", **scope)
        assert state["zone"] == "Asia/Vladivostok"
        await act(
            client,
            engine,
            1,
            admin,
            "save",
            state,
            event_id,
            rows=ROWS,
            zone="Europe/Berlin",
            preliminary=False,
        )
        async with PostgresUnitOfWork(engine) as uow:
            (await uow.store.get(Concert, event_id)).city = "Самара"
        state = await get(client, 1, "timing", **scope)
        assert state["zone"] == "Europe/Berlin"
        assert len(state["zones"]) < 15
        assert (await act(client, engine, 1, admin, "notify", state, event_id))["queued"] == 2
    telegram = ArticleTelegram()
    async with Bot("123456:TEST", session=telegram) as bot:
        assert await deliver_one_outbox(bot, engine)
    (sent,) = telegram.rich_sent
    article = sent.rich_message.model_dump(exclude_none=True)
    assert "Europe/Berlin" not in str(article)
    cells = article["blocks"][1]["cells"]
    assert [[cell["text"] for cell in row] for row in cells] == [
        ["", "", "31 декабря 2026"],
        ["17:00", "17:45", "Саундчек"],
        ["", "", "01 января 2027"],
        ["00:15", "", "Окончание"],
    ]


def test_timing_keeps_entered_order_and_accepts_existing_precise_times():
    rows = timing_rows(
        [
            {"start": "23:47", "end": "00:12", "label": "Выступление"},
            {"start": "00:15", "label": "Выезд"},
            {"start": "00:15", "label": "Закрытие"},
        ]
    )
    assert [(row["start"], row["end"], row["day"]) for row in rows] == [
        ("23:47", "00:12", 0),
        ("00:15", "", 1),
        ("00:15", "", 1),
    ]
