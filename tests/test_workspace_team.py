from uuid import UUID, uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.groups import Groups
from setlist_bot.application.team import Team
from setlist_bot.domain.entities import GigInvitation, Group, Membership, Outbox, User
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def act(client, engine, actor, session, action, *, succeeds=True, **fields):
    payload = {**session, "action": action, "request_id": str(uuid4()), **fields}
    result = await post(client, actor, "team_action", payload)
    assert result["status"] == "pending"
    assert await deliver_request(engine, result["request"]) is succeeds
    receipt = await get(client, actor, "catalog_status", **session, request=result["request"])
    assert await post(client, actor, "team_action", payload) == receipt
    return receipt


async def test_invitation_inbox_uses_same_membership_and_restricts_personal_session(engine, world):
    await setup_workspace(engine, world)
    band = {"band": str(world.group.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        assert not (await get(client, 3, "bands"))["bands"]
        assert not (await get(client, 3, "team_inbox"))["invitations"]
        await get(client, 3, "team", 403, **band)
        await post(client, 2, "team_recipient", {**band, "reference": "3"}, 403)
        person = await post(client, 1, "team_recipient", {**band, "reference": "3"})
        assert person == {
            "id": str(world.stranger.id),
            "name": "3",
            "username": "",
            "profile_url": "tg://user?id=3",
        }
        admin = await post(client, 1, "open_repertoire", band)
        invited = await act(
            client,
            engine,
            1,
            admin,
            "invite",
            target=person["id"],
            reference="3",
            roles="Менеджер\nСветовик",
        )
        assert invited["invited"]
        assert not (await get(client, 2, "team_inbox"))["invitations"]
        inbox = (await get(client, 3, "team_inbox"))["invitations"]
        assert len(inbox) == 1 and inbox[0]["roles"] == ["Менеджер", "Световик"]
        assert inbox[0]["band"] == world.group.name and not inbox[0]["already_member"]
        selection = {"membership": inbox[0]["id"]}
        await post(client, 2, "open_team_membership", selection, 403)
        personal = await post(client, 3, "open_team_membership", selection)
        await get(client, 3, "song", 403, **personal, song_id=str(world.song.id))
        await post(client, 3, "catalog_save", {**personal, "request_id": str(uuid4())}, 403)
        await post(
            client,
            3,
            "team_action",
            {
                **personal,
                "action": "invite",
                "request_id": str(uuid4()),
            },
            403,
        )
        await act(client, engine, 3, personal, "respond", accept=True, version=inbox[0]["version"])
        assert [row["id"] for row in (await get(client, 3, "bands"))["bands"]] == [band["band"]]
        self_view = await get(client, 3, "team", **band)
        assert len(self_view["members"]) == 1 and not self_view["admin"]
        assert {row["name"] for row in self_view["members"][0]["roles"]} == {"Менеджер", "Световик"}
        await get(
            client,
            3,
            "team_contact",
            403,
            **band,
            contact=self_view["members"][0]["roles"][0]["id"],
        )
        await act(
            client, engine, 1, admin, "invite", target=person["id"], reference="3", roles="Гитара"
        )
        extra = (await get(client, 3, "team_inbox"))["invitations"][0]
        assert extra["already_member"] and extra["id"] == inbox[0]["id"]
        await act(client, engine, 3, personal, "respond", accept=False, version=extra["version"])
        assert (await get(client, 3, "bands"))["bands"]
        async with PostgresUnitOfWork(engine) as uow:
            notices = await uow.store.find(
                Outbox, support_kind="team", chat_id=3, group_id=world.group.id
            )
            assert len(notices) == 2
            assert await uow.store.find(Outbox, support_kind="team_note", chat_id=1)


async def test_requests_role_removal_admin_rights_and_leave_keep_booking(engine, world):
    await setup_workspace(engine, world)
    band = {"band": str(world.group.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", band)
        member = await post(client, 2, "open_repertoire", band)
        await act(client, engine, 2, member, "request_roles", roles="Гитара")
        admin_view = await get(client, 1, "team", **band)
        target = next(row for row in admin_view["members"] if not row["self"])
        assert target["requested"] == ["Гитара"]
        args = {"target": target["id"], "version": target["version"]}
        await post(
            client,
            2,
            "team_action",
            {
                **member,
                **args,
                "action": "approve",
                "request_id": str(uuid4()),
            },
            403,
        )
        await act(client, engine, 1, admin, "approve", **args)
        stale = await act(client, engine, 1, admin, "reject", succeeds=False, **args)
        assert stale["error"] == "input_expired"
        team = await get(client, 1, "team", **band)
        target = next(row for row in team["members"] if not row["self"])
        card = await get(client, 1, "team_contact", **band, contact=target["roles"][0]["id"])
        assert (card["band"], card["language"]) == (team["band"], team["language"])
        await act(
            client, engine, 1, admin, "role_visibility", target=card["id"], version=card["version"]
        )
        card = await get(client, 1, "team_contact", **band, contact=card["id"])
        assert card["hidden"]
        await act(
            client, engine, 1, admin, "role_remove", target=card["id"], version=card["version"]
        )
        own = await get(client, 2, "team", **band)
        assert own["members"][0]["roles"] == []
        await act(
            client, engine, 1, admin, "grant", target=target["id"], version=own["own"]["version"]
        )
        assert (await get(client, 2, "team", **band))["admin"]
        personal = await post(client, 2, "open_team_membership", {"membership": target["id"]})
        preview = await act(
            client, engine, 2, personal, "leave_preview", version=own["own"]["version"]
        )
        assert [row["title"] for row in preview["bookings"]] == [world.concert.title]
        await act(client, engine, 2, personal, "leave", version=own["own"]["version"])
        assert (await get(client, 2, "bands"))["bands"] == []
        async with PostgresUnitOfWork(engine) as uow:
            bookings = await uow.store.find(
                GigInvitation, concert_id=world.concert.id, user_id=world.member.id
            )
            assert bookings[0].status == "confirmed"


async def test_invite_rechecks_target_and_actor_and_transfer_checks_owner(engine, world):
    await setup_workspace(engine, world)
    group_id = world.group.id
    band = {"band": str(group_id)}
    async with PostgresUnitOfWork(engine) as uow:
        person = await uow.store.get(User, world.stranger.id)
        from setlist_bot.domain.entities import now

        person.username, person.username_seen_at = "performer", now()
        await Groups(uow.store, world.admin).administrator(
            group_id,
            (await uow.store.find(Membership, group_id=group_id, user_id=world.member.id))[0].id,
            True,
        )
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", band)
        promoted = await post(client, 2, "open_repertoire", band)
        original = await post(client, 2, "team_recipient", {**band, "reference": "@performer"})
        assert original["id"] == str(world.stranger.id)
        async with PostgresUnitOfWork(engine) as uow:
            fresh = await uow.store.get(User, world.admin.id)
            fresh.username, fresh.username_seen_at = "performer", now()
        stale = await act(
            client,
            engine,
            2,
            promoted,
            "invite",
            succeeds=False,
            target=original["id"],
            reference="@performer",
            roles="Световик",
        )
        assert stale["error"] == "conflict"
        view = await get(client, 1, "team", **band)
        new = next(row for row in view["members"] if not row["self"])
        payload = {"target": new["id"], "version": new["version"]}
        misspelled = await act(
            client, engine, 1, admin, "transfer", succeeds=False, **payload, confirmation="Other"
        )
        assert misspelled["error"] == "confirmation"
        revoked_choice = await post(
            client,
            2,
            "team_action",
            {
                **promoted,
                "action": "invite",
                "request_id": str(uuid4()),
                "target": original["id"],
                "reference": "3",
                "roles": "Вокал",
            },
        )
        await act(client, engine, 1, admin, "revoke", **payload)
        assert not await deliver_request(engine, revoked_choice["request"])
        assert not (await get(client, 3, "team_inbox"))["invitations"]
        await act(client, engine, 1, admin, "transfer", **payload, confirmation=world.group.name)
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.get(Group, group_id)).owner_id == world.member.id
            assert (await uow.store.get(Membership, UUID(new["id"]))).role == "admin"


async def test_inbox_withdraw_retry_and_responses_are_versioned_across_clients(engine, world):
    await setup_workspace(engine, world)
    band = {"band": str(world.group.id)}
    async with PostgresUnitOfWork(engine) as uow:
        invitation = await Team(uow.store, world.admin).invite(
            world.group.id, world.stranger.id, ["Бас"]
        )
        key = {"target": str(invitation.id), "version": invitation.version}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", band)
        personal = await post(client, 3, "open_team_membership", {"membership": key["target"]})
        await act(client, engine, 1, admin, "retry", **key)
        old = await act(
            client,
            engine,
            3,
            personal,
            "respond",
            succeeds=False,
            accept=True,
            version=key["version"],
        )
        assert old["error"] == "input_expired"
        fresh = (await get(client, 3, "team_inbox"))["invitations"][0]
        await act(
            client, engine, 1, admin, "withdraw", target=fresh["id"], version=fresh["version"]
        )
        assert not (await get(client, 3, "team_inbox"))["invitations"]
        assert not (await get(client, 3, "bands"))["bands"]
