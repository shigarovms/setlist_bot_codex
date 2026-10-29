from datetime import timedelta
from html import unescape
from urllib.parse import quote

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from test_admin import extract, login, settings

from setlist_bot.admin.app import create_app
from setlist_bot.admin.views import actor_link, person
from setlist_bot.application.administration import Administration
from setlist_bot.application.user_lookup import known_user
from setlist_bot.domain.entities import AdminAudit, Group, Membership, User, now
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.admin_queries import AdminQueries
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def known(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        user = await uow.store.get(User, world.member.id)
        user.username, user.username_seen_at = "guitar_player", now()
        user.display_name = "Гитарист"


async def test_observed_usernames_are_updated_on_messages_removed_and_not_taken_from_text(engine):
    processor = Processor(engine)
    await processor.process(1, 1001, 1001, "/start", private=True, sender_username="Some_User")
    async with PostgresUnitOfWork(engine) as uow:
        user = (await uow.store.find(User, telegram_id=1001))[0]
        assert user.username == "some_user" and user.username_seen_at
    await processor.process(2, 1001, 1001, "/help @another_user", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(User, telegram_id=1001))[0].username == "some_user"
    await processor.process(3, 1001, 1001, "/start", private=True, sender_username="")
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(User, telegram_id=1001))[0].username == ""


async def test_known_handle_resolution_prefers_latest_observation_and_rejects_unknown(
    engine, world
):
    await known(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        previous = await uow.store.get(User, world.stranger.id)
        previous.username, previous.username_seen_at = "guitar_player", now() - timedelta(days=1)
        assert (await known_user(uow.store, "@GUITAR_PLAYER")).id == world.member.id
        assert (await known_user(uow.store, "2")).id == world.member.id
        with pytest.raises(AppError, match="admin_user_unknown"):
            await known_user(uow.store, "@unknown_person")
        with pytest.raises(AppError):
            await known_user(uow.store, "https://evil.invalid")


async def test_admin_can_grant_revoke_and_transfer_ownership_by_known_handle(engine, world):
    await known(engine, world)
    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        await login(browser)
        page = await browser.get(f"/groups/{world.group.id}/permissions?user=@GUITAR_PLAYER")
        html = await page.text()
        assert "Назначить администратором" in html and "tg://user?id=2" in html
        action = f"/actions/group_administrator/{world.group.id}"
        form = extract(html, action)
        form["confirmed"] = "yes"
        response = await browser.post(action, data=form, allow_redirects=False)
        assert response.status == 303
        # Duplicate form cannot toggle/repeat the role action.
        assert (await browser.post(action, data=form, allow_redirects=False)).status == 303
        async with PostgresUnitOfWork(engine) as uow:
            member = (
                await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
            )[0]
            assert member.role == "admin"
        page = await browser.get(f"/groups/{world.group.id}/permissions?user=2")
        form = extract(await page.text(), action)
        form["confirmed"] = "yes"
        assert (await browser.post(action, data=form, allow_redirects=False)).status == 303
        async with PostgresUnitOfWork(engine) as uow:
            member = (
                await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
            )[0]
            assert member.role == "member"
            member_id = member.id
        owner_url = f"/groups/{world.group.id}/owner?member={member_id}&reference=" + quote(
            "@guitar_player"
        )
        page = await browser.get(owner_url)
        action = f"/actions/group_owner/{world.group.id}"
        form = extract(await page.text(), action)
        form["confirmation"] = world.group.name
        assert (await browser.post(action, data=form, allow_redirects=False)).status == 303
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.get(Group, world.group.id)).owner_id == world.member.id


async def test_role_assignment_rejects_stale_handle_wrong_group_owner_and_missing_confirmation(
    engine, world
):
    await known(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        values = {
            "member_id": str(member.id),
            "expected_owner": str(world.admin.id),
            "expected_role": "member",
            "operation": "grant",
            "confirmed": "yes",
            "reference": "@guitar_player",
        }
        data = (member.id, values)
    for failure in ("confirmation", "group", "handle", "owner"):
        async with PostgresUnitOfWork(engine) as uow:
            values = dict(data[1])
            target = world.group.id
            if failure == "confirmation":
                values["confirmed"] = "no"
            elif failure == "group":
                target = world.other.id
            elif failure == "owner":
                values["expected_owner"] = str(world.stranger.id)
            else:
                values["reference"] = "@unknown_person"
            with pytest.raises(AppError):
                await Administration(uow.store, "admin").execute(
                    "group_administrator", target, values, "reject-" + failure
                )
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Membership, data[0])).role == "member"


async def test_admin_user_links_search_and_escaping(engine, world):
    await known(engine, world)
    rows = await AdminQueries(engine).listing("users", query="@guitar")
    assert len(rows) == 1 and rows[0]["id"] == world.member.id
    unsafe = person(2, '<script>alert("x")</script>', '" onclick="bad')
    assert (
        "<script>" not in unsafe and "onclick=" not in unsafe and 'href="tg://user?id=2"' in unsafe
    )
    assert "tg://user?id=2" in actor_link("telegram:2")
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(
            AdminAudit(
                actor="telegram:2",
                source="telegram",
                action="test",
                target=str(world.group.id),
                request_key="identity-links",
                details={"old_owner": str(world.admin.id), "new_owner": str(world.member.id)},
            )
        )
    async with TestClient(
        TestServer(create_app(settings(engine), engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        assert (await browser.get("/users", allow_redirects=False)).status == 303
        await login(browser)
        for url in (
            "/users",
            "/groups",
            f"/groups/{world.group.id}",
            f"/users/{world.member.id}",
            "/audit",
        ):
            page = await browser.get(url)
            html = unescape(await page.text())
            assert page.status == 200 and "tg://user?id=" in html
        page = await browser.get(f"/groups/{world.other.id}/permissions?user=2")
        assert "ещё не подтверждённый участник" in await page.text()


async def test_reassigned_username_cannot_change_the_person_selected_in_preview(engine, world):
    await known(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        values = {
            "member_id": str(member.id),
            "expected_owner": str(world.admin.id),
            "expected_role": "member",
            "operation": "grant",
            "confirmed": "yes",
            "reference": "@guitar_player",
        }
        candidate = await uow.store.get(User, world.stranger.id)
        candidate.username, candidate.username_seen_at = "guitar_player", now()
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="conflict"):
            await Administration(uow.store, "admin").execute(
                "group_administrator", world.group.id, values, "changed-handle"
            )
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        assert member.role == "member"
