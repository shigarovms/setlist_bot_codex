from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from conftest import invite_fixture_member
from pydantic import SecretStr
from test_miniapp import settings
from test_repertoire_workspace_actions import get, headers, post
from test_repertoire_workspace_music import deliver_request
from test_staffing import invite, setup_slots
from test_telegram import FakeTelegram
from test_workspace_exports import execute, pdf_text
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import GigInvitation, Membership, Outbox, PersonalDraft
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.miniapp.app import create_app
from setlist_bot.miniapp.browser_login import FLOW_COOKIE, SESSION_COOKIE, BrowserLogin


async def respond(client, engine, session, version, answer, *, ok=True, **extra):
    payload = {
        **session,
        "version": version,
        "answer": answer,
        "request_id": str(uuid4()),
        **extra,
    }
    request = await post(client, 3, "gig_reply", payload)
    assert await deliver_request(engine, request["request"]) is ok
    receipt = await get(client, 3, "catalog_status", **session, request=request["request"])
    assert await post(client, 3, "gig_reply", payload) == receipt
    return receipt


async def test_personal_schedule_keeps_past_bookings_in_history(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        date = today()
        past = await lists.create(
            world.group.id, date - timedelta(days=2), "Прошедший концерт", city="Тула"
        )
        upcoming = await lists.create(world.group.id, date + timedelta(days=2), "Ближайший концерт")
        pending = await lists.create(world.group.id, date + timedelta(days=4), "Новое приглашение")
        await invite_fixture_member(uow.store, past, world.stranger)
        await invite_fixture_member(uow.store, upcoming, world.stranger)
        await invite_fixture_member(uow.store, pending, world.stranger, status="pending")
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        schedule = await get(client, 3, "my_gigs", section="schedule")
        assert schedule["pending_invitations"] == 1
        assert [row["title"] for row in schedule["invitations"]] == ["Ближайший концерт"]
        history = await get(client, 3, "my_gigs", section="history")
        assert [(row["title"], row["city"], row["status"]) for row in history["invitations"]] == [
            ("Прошедший концерт", "Тула", "confirmed")
        ]
        card = await get(client, 3, "gig_card", invitation=history["invitations"][0]["id"])
        assert card["invitation"]["title"] == "Прошедший концерт"


async def test_guest_has_only_own_invitation_and_can_read_and_export_approved_setlist(
    engine, world
):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    invite_id = await invite(engine, world.admin, slot, world.stranger.id)
    invitation = {"invitation": str(invite_id)}
    band, concert = world.group.id, world.concert.id
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(band, concert, 0, ["Комета"])
        await lists.approve(band, concert, 1)
        await lists.edit_songs(band, concert, 0, ["2 мин"], private=True)
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        assert not (await get(client, 3, "bands"))["bands"]
        assert not any(
            row["id"] == str(invite_id) for row in (await get(client, 2, "my_gigs"))["invitations"]
        )
        assert (await get(client, 3, "my_gigs"))["invitations"][0]["id"] == str(invite_id)
        await get(client, 1, "gig_card", 403, **invitation)
        await get(client, 2, "gig_card", 403, **invitation)
        await post(client, 2, "open_gig", invitation, 403)
        card = (await get(client, 3, "gig_card", **invitation))["invitation"]
        assert card["status"] == "pending" and card["answers"] == ["yes", "no", "conditional"]
        await get(client, 3, "gig_setlist", 403, **invitation)
        session = await post(client, 3, "open_gig", invitation)
        await get(client, 3, "song", 403, **session, song_id=str(world.song.id))
        await get(client, 3, "state", 403, **session)
        await post(client, 3, "roster_action", {**session, "request_id": str(uuid4())}, 403)
        await get(client, 3, "repertoire", 403, band=str(band))
        create = {"action": "generate", "version": 2, "audience": "musicians", "language": "ru"}
        assert (await execute(client, engine, 3, session, create, ok=False))["error"] == "forbidden"
        await respond(client, engine, session, card["version"], "yes")
        booked = (await get(client, 3, "my_gigs", section="confirmed"))["invitations"]
        assert [row["id"] for row in booked] == [str(invite_id)]
        assert not (await get(client, 3, "my_gigs"))["invitations"]
        shared = await get(client, 3, "gig_setlist", **invitation)
        assert shared["approved"] and shared["seconds"] == 218
        assert [row["title"] for row in shared["entries"]] == ["Комета"]
        file = await execute(client, engine, 3, session, create)
        route = "/miniapp/api/export_download"
        params = {**session, "request": file["request"]}
        received = await client.get(route, params=params, headers=headers(3))
        assert received.status == 200 and "Комета" in pdf_text(await received.read())
        assert (await client.get(route, params=params, headers=headers(2))).status == 401
        async with PostgresUnitOfWork(engine) as uow:
            row = await uow.store.get(GigInvitation, invite_id)
            assert row.status == "confirmed"
            assert not await uow.store.find(Membership, group_id=band, user_id=world.stranger.id)
            assert not await uow.store.find(PersonalDraft, owner_id=world.stranger.id)
            await Setlists(uow.store, world.admin).reopen(band, concert, 2)
        assert (await client.get(route, params=params, headers=headers(3))).status == 403
        async with PostgresUnitOfWork(engine) as uow:
            await Staffing(uow.store, world.admin).cancel_invitation(invite_id, row.version)
        await get(client, 3, "gig_setlist", 403, **invitation)
        await get(client, 3, "gig_card", 403, **invitation)
        assert (await client.get(route, params=params, headers=headers(3))).status == 403
        history = (await get(client, 3, "my_gigs", section="history"))["invitations"]
        assert history[0]["status"] == "cancelled" and history[0]["title"] == ""


async def test_conditional_new_date_and_busy_confirmation_are_bound_to_version_and_session(
    engine, world
):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    invite_id = await invite(engine, world.admin, slot, world.stranger.id)
    invitation = {"invitation": str(invite_id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 3, "open_gig", invitation)
        card = (await get(client, 3, "gig_card", **invitation))["invitation"]
        await respond(client, engine, session, card["version"], "conditional")
        before = (await get(client, 3, "gig_card", **invitation))["invitation"]
        changed = world.concert.date + timedelta(days=3)
        async with PostgresUnitOfWork(engine) as uow:
            await ConcertDetails(uow.store, world.admin).update(
                world.group.id, world.concert.id, "concert_date", changed.isoformat()
            )
            await Staffing(uow.store, world.stranger).busy(changed, changed)
        stale = await respond(client, engine, session, before["version"], "yes", ok=False)
        assert stale["error"] == "input_expired"
        new = (await get(client, 3, "gig_card", **invitation))["invitation"]
        assert new["status"] == "reconfirm" and new["date"] == changed.isoformat()
        busy = await respond(client, engine, session, new["version"], "yes", ok=False)
        assert busy["error"] == "staff_busy"
        another = await post(client, 3, "open_gig", invitation)
        refused = await respond(
            client, engine, another, new["version"], "yes", ok=False, busy_receipt=busy["request"]
        )
        assert refused["error"] == "input_expired"
        final = await respond(
            client, engine, session, new["version"], "yes", busy_receipt=busy["request"]
        )
        assert final["invitation_status"] == "confirmed"
        async with PostgresUnitOfWork(engine) as uow:
            rows = await uow.store.find(
                Outbox, group_id=world.group.id, chat_id=1, support_kind="staffing"
            )
            assert any(row.payload["kind"] == "reply" for row in rows)


async def test_invitation_button_and_browser_login_open_same_guest_invitation(
    engine, world, monkeypatch
):
    await setup_workspace(engine, world)
    slot, _ = await setup_slots(engine, world)
    invite_id = await invite(engine, world.admin, slot, world.stranger.id)
    monkeypatch.setenv("MINIAPP_PUBLIC_BASE_URL", "https://example.org")
    fake = FakeTelegram()
    async with Bot("123456:TEST", session=fake) as bot:
        for _ in range(12):
            await deliver_one_outbox(bot, engine)
            if fake.sent:
                break
    assert fake.sent and fake.sent[-1].chat_id == 3
    webapp = next(
        button.web_app
        for row in fake.sent[-1].reply_markup.inline_keyboard
        for button in row
        if button.web_app
    )
    assert parse_qs(urlsplit(webapp.url).query)["invitation"] == [str(invite_id)]
    config = settings(engine).model_copy(update={"browser_login_secret": SecretStr("test-only")})

    async def exchange(*_args):
        return 3

    monkeypatch.setattr(BrowserLogin, "exchange", exchange)
    async with TestClient(TestServer(create_app(config, engine))) as client:
        begin = await client.get(
            "/miniapp/auth/login", params={"invitation": str(invite_id)}, allow_redirects=False
        )
        nonce = parse_qs(urlsplit(begin.headers["Location"]).query)["state"][0]
        assert "invitation" not in begin.headers["Location"]
        finish = await client.get(
            "/miniapp/auth/callback",
            params={"state": nonce, "code": "known"},
            headers={"Cookie": f"{FLOW_COOKIE}={begin.cookies[FLOW_COOKIE].value}"},
            allow_redirects=False,
        )
        assert finish.headers["Location"].endswith(f"&invitation={invite_id}")
        response = await client.get(
            "/miniapp/api/gig_card",
            params={"invitation": str(invite_id)},
            headers={"Cookie": f"{SESSION_COOKIE}={finish.cookies[SESSION_COOKIE].value}"},
        )
        assert (
            response.status == 200 and (await response.json())["invitation"]["status"] == "pending"
        )
