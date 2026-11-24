import json
from uuid import UUID, uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_setlist_menus import sender
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.member_boards import queue_member_board
from setlist_bot.application.profiles import Profiles
from setlist_bot.application.workspace_exports import WorkspaceExports
from setlist_bot.domain.entities import Confirmation, Outbox, ProfessionalRole, User
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def action(client, engine, actor, session, action_name, *, ok=True, **fields):
    payload = {**session, "action": action_name, "request_id": str(uuid4()), **fields}
    queued = await post(client, actor, "profile_action", payload)
    assert queued["status"] == "pending"
    assert await deliver_request(engine, queued["request"]) is ok
    receipt = await get(client, actor, "catalog_status", **session, request=queued["request"])
    assert await post(client, actor, "profile_action", payload) == receipt
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(Confirmation, UUID(queued["request"]))
        assert "profile" not in json.loads(row.command)
    return receipt


async def test_web_profile_is_private_and_uses_versioned_bot_profile_and_team_board(engine, world):
    await setup_workspace(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        await queue_member_board(uow.store, world.group.id, ensure=True)
        await uow.store.flush()
        for event in await uow.store.find(Outbox, support_kind="members"):
            event.sent = True
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        member = {"session": (await post(client, 2, "open_account", {}))["session"]}
        band = await post(client, 2, "open_repertoire", {"band": str(world.group.id)})
        original = await get(client, 2, "profile")
        fields = {
            "name": "Сценическое имя",
            "city": "Пермь",
            "substitutions": True,
            "travel": False,
            "about": "Обо мне\nВторая строка",
        }
        command = {"action": "profile_save", "version": 0, "patch": fields}
        await post(client, 1, "profile_action", {**member, **command}, 401)
        await post(client, 2, "profile_action", {**band, **command}, 403)
        saved = await action(client, engine, 2, member, "profile_save", version=0, patch=fields)
        assert saved["version"] == original["version"] + 1
        assert (await get(client, 2, "profile"))["profile"] == {**fields, "roles": []}
        assert (await get(client, 1, "profile", user_id=str(world.member.id)))["profile"] == {}
        assert (await get(client, 3, "profile"))["profile"] == {}
        telegram = await sender(engine)("/profile", actor=2, private=True)
        assert "Сценическое имя" in telegram.text and "Пермь" in telegram.text
        async with PostgresUnitOfWork(engine) as uow:
            user = await uow.store.get(User, world.member.id)
            assert user.display_name == fields["name"]
            events = await uow.store.find(
                Outbox, group_id=world.group.id, support_kind="members", sent=False
            )
            assert len(events) == 1
            await Profiles(uow.store, user).save(1, {**user.profile, "city": "Томск"})
        stale = await action(
            client, engine, 2, member, "profile_save", ok=False, version=1, patch={"city": "Москва"}
        )
        assert stale["error"] == "conflict"
        assert (await get(client, 2, "profile"))["profile"]["city"] == "Томск"
        await action(client, engine, 2, member, "profile_save", version=2, patch={"travel": None})
        profile = (await get(client, 2, "profile"))["profile"]
        assert "travel" not in profile and profile["substitutions"] is True
        assert profile["about"] == fields["about"]


async def test_profile_roles_require_explicit_choices_and_preserve_order_and_custom_names(
    engine, world
):
    await setup_workspace(engine, world)
    source = "басист, звук, Баритон без микрофона"
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        guest = {"session": (await post(client, 3, "open_account", {}))["session"]}
        plan = await post(client, 3, "profile_roles", {"version": 0, "source": source})
        bass, sound, custom = plan["roles"]
        assert bass == {"source": "басист", "title": "Бас-гитара", "choices": [], "sound": False}
        assert sound["sound"] and sound["title"] is None
        assert sound["choices"] == [
            "Звукорежиссёр FOH",
            "Мониторный звукорежиссёр",
            "Звукорежиссёр",
        ]
        assert custom["title"] is None and custom["source"] in custom["choices"]
        chosen = [bass["title"], sound["choices"][1], custom["source"]]
        roles = {"source": source, "language": plan["language"], "choices": chosen}
        invalid = {**roles, "choices": [bass["title"], "Звуковой техник", custom["source"]]}
        rejected = await action(
            client, engine, 3, guest, "profile_save", ok=False, version=0, patch={}, roles=invalid
        )
        assert rejected["error"] == "input_expired"
        await action(client, engine, 3, guest, "profile_save", version=0, patch={}, roles=roles)
        assert (await get(client, 3, "profile"))["profile"]["roles"] == chosen
        stale = await post(client, 3, "profile_roles", {"version": 0, "source": source}, 409)
        assert stale["error"] == "conflict"
        await action(client, engine, 3, guest, "profile_save", version=1, patch={"city": "Казань"})
        assert (await get(client, 3, "profile"))["profile"]["roles"] == chosen
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(ProfessionalRole, title=custom["source"])
        clear = await post(client, 3, "profile_roles", {"version": 2, "source": ""})
        assert clear["roles"] == []
        await action(
            client,
            engine,
            3,
            guest,
            "profile_save",
            version=2,
            patch={},
            roles={"source": "", "language": clear["language"], "choices": []},
        )
        assert (await get(client, 3, "profile"))["profile"] == {"roles": [], "city": "Казань"}


async def test_interface_language_and_pdf_settings_are_shared_with_bot_and_export(engine, world):
    await setup_workspace(engine, world)
    config = {"font": "serif", "size": 32, "align": "center"}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        guest = {"session": (await post(client, 3, "open_account", {}))["session"]}
        saved = await action(client, engine, 3, guest, "language", language="en", expected="ru")
        assert saved["language"] == (await get(client, 3, "bands"))["language"] == "en"
        send = sender(engine)
        telegram = await send("/groups", actor=3, private=True)
        assert ("👤 My profile", "profile") in telegram.buttons
        english = await post(client, 3, "profile_roles", {"version": 0, "source": "sound"})
        assert english["roles"][0]["choices"][-1] == "Sound engineer"
        await send("/language ru", actor=3, private=True)
        assert (await get(client, 3, "profile"))["language"] == "ru"
        stale = await action(
            client, engine, 3, guest, "language", ok=False, language="en", expected="en"
        )
        assert stale["error"] == "conflict"
        admin = {"session": (await post(client, 1, "open_account", {}))["session"]}
        default = (await get(client, 1, "profile"))["pdf"]["settings"]
        saved = await action(
            client, engine, 1, admin, "pdf_settings", expected=default, settings=config
        )
        assert saved["settings"] == (await get(client, 1, "profile"))["pdf"]["settings"] == config
        stale = await action(
            client, engine, 1, admin, "pdf_settings", ok=False, expected=default, settings=default
        )
        assert stale["error"] == "conflict"
        async with PostgresUnitOfWork(engine) as uow:
            user = await uow.store.get(User, world.admin.id)
            assert user.export_settings == config
            options = await WorkspaceExports(uow.store, user).options(
                world.group.id, world.concert.id
            )
            assert options["settings"] == config
        assert (await get(client, 3, "profile"))["pdf"]["settings"] == default
