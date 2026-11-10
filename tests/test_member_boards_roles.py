"""Registration, permanent-team approval, durable public roster and editable vocabulary."""

import asyncio
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from test_admin import login, settings
from test_collective_workflows import client as collective_client
from test_setlist_menus import sender
from test_telegram import FakeTelegram

from setlist_bot.admin.app import create_app
from setlist_bot.application.groups import Groups
from setlist_bot.application.member_boards import queue_member_board
from setlist_bot.application.profiles import Profiles
from setlist_bot.application.role_dictionary import RoleDictionary
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    AdminAudit,
    MemberBoard,
    Membership,
    Outbox,
    ProfessionalRole,
    StaffContact,
    User,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.commands import menu_commands

TOKEN = "123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN"


async def test_greeting_connects_through_existing_owner_confirmation(engine, world):
    send = collective_client(engine)
    greeting = await send("on_added", actor=2, chat=-300)
    assert ("🎸 Подключить коллектив", "new_band") in greeting.buttons
    assert "любой участник" in greeting.text and "/new_band" in greeting.text
    prompt = await send("new_band", actor=2, chat=-300, owner=1, remember=20)
    assert prompt.force_reply
    preview = await send("Новый коллектив", actor=2, chat=-300, reply=20)
    command = next(a for _, a in preview.buttons if a.startswith("group_create_yes"))
    denied = await send(command, actor=2, chat=-300, owner=1)
    assert "только владелец" in denied.text
    await send(command, actor=1, chat=-300, owner=1)
    async with PostgresUnitOfWork(engine) as uow:
        boards = await uow.store.find(MemberBoard, chat_id=-300)
        assert len(boards) == 1
        assert (
            len(await uow.store.find(Outbox, group_id=boards[0].group_id, support_kind="members"))
            == 1
        )
    existing = await send("on_added", actor=2, chat=-300)
    assert not any(a == "new_band" for _, a in existing.buttons)


async def test_profile_is_first_even_without_bands(engine, world):
    send = sender(engine)
    for actor in (1, 3):
        response = await send("/groups", actor=actor, private=True)
        assert response.buttons[0] == ("👤 Мой профиль", "profile")
    assert "profile" in [c.command for c in menu_commands("private", "ru")]
    response = await send("/profile", actor=3, private=True)
    assert not response.force_reply and "Как вас зовут" in response.text


async def test_role_request_is_prompt_bound_and_requires_admin_approval(engine, world):
    send = sender(engine)
    response = await send(f"join_band {world.group.id.hex}", actor=3, remember=40)
    assert response.force_reply and "Какие роли" in response.text
    # Unrelated messages cannot become membership requests.
    await send("басист", actor=3)
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(
            Membership, group_id=world.group.id, user_id=world.stranger.id
        )
    preview = await send("басист, гитарист", actor=3, reply=40)
    approve = next(a for _, a in preview.buttons if a.startswith("member yes"))
    forbidden = await send(approve, actor=2)
    assert "администратор" in forbidden.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(
            StaffContact, group_id=world.group.id, user_id=world.stranger.id
        )
    await send(approve)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.stranger.id)
        )[0]
        assert (
            member.status == "active" and member.role == "member" and member.requested_roles is None
        )
        assert (
            len(
                await uow.store.find(
                    StaffContact, group_id=world.group.id, user_id=world.stranger.id, permanent=True
                )
            )
            == 2
        )
    duplicate = await send(approve)
    assert "уже" in duplicate.text.lower() or "устар" in duplicate.text.lower()
    foreign = await send(f"join_band {world.group.id.hex}", actor=3, chat=-200)
    assert not foreign.force_reply and "не найден" in foreign.text.lower()


async def test_changed_role_request_rejects_old_approval_and_cancel_keeps_membership(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Groups(uow.store, world.member)
        member = await service.request_roles(world.group.id, ["Гитара"])
        old_version = member.version
    async with PostgresUnitOfWork(engine) as uow:
        member = await Groups(uow.store, world.member).request_roles(world.group.id, ["Бас-гитара"])
        version = member.version
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="input_expired"):
            await Groups(uow.store, world.admin).decide_member(
                world.group.id, member.id, True, old_version
            )
    async with PostgresUnitOfWork(engine) as uow:
        await Groups(uow.store, world.admin).decide_member(
            world.group.id, member.id, False, version
        )
    async with PostgresUnitOfWork(engine) as uow:
        current = await uow.store.get(Membership, member.id)
        assert current.status == "active" and current.requested_roles is None
        assert not await uow.store.find(StaffContact, user_id=world.member.id)


async def prepare_board(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await queue_member_board(uow.store, world.group.id, ensure=True)
        await queue_member_board(uow.store, world.group.id)
        assert (
            len(
                await uow.store.find(
                    Outbox, group_id=world.group.id, support_kind="members", sent=False
                )
            )
            == 1
        )


async def test_board_updates_same_message_after_role_and_name_changes(engine, world):
    await prepare_board(engine, world)
    session = FakeTelegram()
    async with Bot(TOKEN, session=session) as bot:
        assert await deliver_one_outbox(bot, engine)
        assert len(session.sent) == 1 and not session.edits
        async with PostgresUnitOfWork(engine) as uow:
            service = Staffing(uow.store, world.admin)
            role = await service.role(world.group.id, "Басист")
            await service.set_permanent(world.group.id, role.id, world.member.id)
        assert await deliver_one_outbox(bot, engine)
        assert len(session.sent) == 1 and "Басист" in session.edits[-1].text
        async with PostgresUnitOfWork(engine) as uow:
            await Profiles(uow.store, world.member).save(
                0,
                {
                    "name": "Артист",
                    "roles": ["Бас"],
                    "city": "Секретный город",
                    "about": "Не для чата",
                },
            )
        assert await deliver_one_outbox(bot, engine)
        assert "Артист" in session.edits[-1].text
        assert (
            "Секретный" not in session.edits[-1].text
            and "Не для чата" not in session.edits[-1].text
        )
        async with PostgresUnitOfWork(engine) as uow:
            contact = (await uow.store.find(StaffContact, permanent=True, group_id=world.group.id))[
                0
            ]
            await Staffing(uow.store, world.admin).change_contact(
                contact.id, contact.version, remove=True
            )
        assert await deliver_one_outbox(bot, engine)
        assert "Басист" not in session.edits[-1].text
        assert len(session.sent) == 1


async def test_deleted_board_is_replaced_and_wrong_chat_is_never_edited(engine, world):
    await prepare_board(engine, world)
    session = FakeTelegram()
    async with Bot(TOKEN, session=session) as bot:
        await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        await queue_member_board(uow.store, world.group.id)

    class Missing(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, EditMessageText):
                raise TelegramBadRequest(method=method, message="message to edit not found")
            return await super().make_request(bot, method, timeout)

    replacement = Missing()
    async with Bot(TOKEN, session=replacement) as bot:
        assert await deliver_one_outbox(bot, engine)
    assert len(replacement.sent) == 1
    async with PostgresUnitOfWork(engine) as uow:
        board = (await uow.store.find(MemberBoard, group_id=world.group.id))[0]
        board.group_epoch += 1
        # A stale queued job must not recreate an invitation in an old lifecycle/chat.
        uow.store.add(
            Outbox(group_id=world.group.id, chat_id=-100, support_kind="members", text="test")
        )
    async with Bot(TOKEN, session=FakeTelegram()) as bot:
        assert not await deliver_one_outbox(bot, engine)


async def test_dictionary_alias_changes_preserve_old_skills_and_local_spelling(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        dictionary = RoleDictionary(uow.store)
        role = next(r for r in await dictionary.entries() if r.key == "bass")
        await dictionary.save(role.id, role.version, "Басист на сцене", ["низкие струны"])
    async with PostgresUnitOfWork(engine) as uow:
        index = await RoleDictionary(uow.store).index()
        assert index.key("bass player") == index.key("низкие струны") == "bass"
        assert index.parse("bass player") == ["Басист на сцене"]
        service = Staffing(uow.store, world.admin)
        first = await service.role(world.group.id, "Бас")
        second = await service.role(world.group.id, "низкие струны")
        assert first.id == second.id and first.name == "Бас"
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="conflict"):
            await RoleDictionary(uow.store).save(role.id, 1, "Другой бас", [])
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="role_dictionary_collision"):
            await RoleDictionary(uow.store).save(uuid4(), 0, "Другая роль", ["bass player"])


async def test_dictionary_serializes_competing_alias_assignments(engine):
    async def create(title):
        try:
            async with PostgresUnitOfWork(engine) as uow:
                await RoleDictionary(uow.store).save(uuid4(), 0, title, ["unique-skill"])
            return "ok"
        except AppError as exc:
            return exc.code

    assert sorted(await asyncio.gather(create("А"), create("Б"))) == [
        "ok",
        "role_dictionary_collision",
    ]


async def test_admin_dictionary_forms_are_signed_audited_and_readonly_guarded(engine, world):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    config = settings(engine)
    async with TestClient(
        TestServer(create_app(config, engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        await login(browser)
        page = await (await browser.get("/roles")).text()
        from test_admin import Forms

        form = next(f for f in Forms(page).forms if f["action"].startswith("/actions/role_save/"))
        fields = {
            **form["data"],
            "title": "Технический директор",
            "aliases": "technical director\nтехдир",
        }
        response = await browser.post(
            form["action"], data={**fields, "proof": "0" * 64}, allow_redirects=False
        )
        assert response.status == 403
        response = await browser.post(form["action"], data=fields, allow_redirects=False)
        assert response.status == 303 and response.headers["Location"].startswith("/roles")
        duplicate = await browser.post(form["action"], data=fields, allow_redirects=False)
        assert duplicate.status == 303 and "duplicate" in duplicate.headers["Location"]
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(ProfessionalRole, title="Технический директор")) == 1
        assert len(await uow.store.find(AdminAudit, action="role_save")) == 1
    config = config.model_copy(update={"read_only": True})
    async with TestClient(
        TestServer(create_app(config, engine)), cookie_jar=CookieJar(unsafe=True)
    ) as browser:
        await login(browser)
        assert 'name="title"' not in await (await browser.get("/roles")).text()
        assert (await browser.post(form["action"], data=fields)).status == 403


async def test_long_public_board_reuses_all_pages(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        for i in range(150):
            user = User(
                telegram_id=10000 + i, display_name=f"Музыкант с достаточно длинным именем {i}"
            )
            uow.store.add(user)
            await uow.store.flush()
            uow.store.add(Membership(group_id=world.group.id, user_id=user.id, status="active"))
        await queue_member_board(uow.store, world.group.id, ensure=True)
    session = FakeTelegram()
    async with Bot(TOKEN, session=session) as bot:
        assert await deliver_one_outbox(bot, engine)
        count = len(session.sent)
        assert count > 1
        assert "149" in "".join(m.text for m in session.sent)
        async with PostgresUnitOfWork(engine) as uow:
            await queue_member_board(uow.store, world.group.id)
        assert await deliver_one_outbox(bot, engine)
        assert len(session.sent) == count and len(session.edits) == count
        async with PostgresUnitOfWork(engine) as uow:
            board = (await uow.store.find(MemberBoard, group_id=world.group.id))[0]
            assert len(board.message_ids) == count


async def test_new_migration_preserves_existing_rows_and_blocks_loss_of_role_requests(
    database_url, engine, world
):
    from test_migrations import alembic

    async with PostgresUnitOfWork(engine) as uow:
        await Groups(uow.store, world.member).request_roles(world.group.id, ["Гитара"])
    failed = alembic(database_url, "downgrade", "f23a91c8d604", check=False)
    assert failed.returncode != 0 and "Downgrade would lose membership" in failed.stderr
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        assert member.requested_roles == ["Гитара"]
        assert len(await uow.store.find(User)) == 3


async def test_artist_confirmation_shows_three_songs_without_genre(engine, world):
    from setlist_bot.domain.entities import MusicSearch
    from setlist_bot.transport.controller import Controller
    from setlist_bot.transport.music_search import music_view

    async with PostgresUnitOfWork(engine) as uow:
        job = MusicSearch(
            group_id=world.group.id,
            owner_id=world.admin.id,
            chat_id=1,
            group_epoch=0,
            query="Marytale",
            expires_at=now(),
            status="artists",
            data={
                "artists": [
                    {
                        "name": "Marytale",
                        "genre": "Секретный жанр",
                        "examples": ["Первая", "Вторая", "Третья", "Четвёртая"],
                    }
                ]
            },
        )
        c = Controller(uow.store, world.admin, 1, private=True)
        response = await music_view(c, job)
        assert "Песни коллектива:\nПервая\nВторая\nТретья" in response.text
        assert "Секретный жанр" not in response.text and "Четвёртая" not in response.text
        assert response.buttons[0][0] == "Marytale"


async def test_profile_change_during_delivery_cannot_lose_roster_refresh(engine, world):
    from aiogram.methods import SendMessage

    await prepare_board(engine, world)
    sending, release = asyncio.Event(), asyncio.Event()

    class Slow(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, SendMessage):
                sending.set()
                await release.wait()
            return await super().make_request(bot, method, timeout)

    session = Slow()
    async with Bot(TOKEN, session=session) as bot:
        delivery = asyncio.create_task(deliver_one_outbox(bot, engine))
        profile_ready = asyncio.Event()

        async def rename():
            async with PostgresUnitOfWork(engine) as uow:
                await Profiles(uow.store, world.member).save(0, {"name": "Имя во время отправки"})
                profile_ready.set()
            # The outbox FK may wait for the worker's Group lock at commit.

        editing = None
        try:
            async with asyncio.timeout(10):
                await sending.wait()
                editing = asyncio.create_task(rename())
                await profile_ready.wait()
        finally:
            release.set()
            await delivery
            if editing:
                await editing
        # A separate committed event must remain even though the old delivery was pending.
        assert await deliver_one_outbox(bot, engine)
        assert "Имя во время отправки" in session.edits[-1].text
        assert len(session.sent) == 1


async def test_join_roles_preserve_spelling_without_inventing_professional_skills(engine, world):
    from setlist_bot.domain.entities import StaffRole

    async with PostgresUnitOfWork(engine) as uow:
        member = await Groups(uow.store, world.member).request_roles(
            world.group.id, ["Звукорежиссёр, звукарь"]
        )
        assert member.requested_roles == ["Звукорежиссёр"]
        await Groups(uow.store, world.admin).decide_member(
            world.group.id, member.id, True, member.version
        )
        contacts = await uow.store.find(
            StaffContact, group_id=world.group.id, user_id=world.member.id, permanent=True
        )
        assert len(contacts) == 1
        assert (await uow.store.get(StaffRole, contacts[0].role_id)).name == "Звукорежиссёр"
        user = await uow.store.get(User, world.member.id)
        assert not user.profile.get("roles")
        index = await RoleDictionary(uow.store).index()
        assert not index.matches("MON", "Звукорежиссёр")
