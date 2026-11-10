import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat, TelegramRetryAfter
from aiogram.methods import GetMyCommands
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeChatAdministrators,
    BotCommandScopeChatMember,
)

from setlist_bot import main
from setlist_bot.config import Settings
from setlist_bot.domain.entities import Context, Group
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.commands import (
    DESCRIPTIONS,
    GLOBAL_SCOPES,
    MENUS,
    CommandSyncError,
    CommandTargets,
    collect_command_targets,
    menu_commands,
    scope_key,
    synchronize_commands,
)


class FakeCommandAPI:
    def __init__(self):
        self.menus = {}
        self.calls = []
        self.unavailable = set()
        self.fail_registration = False
        self.drop_writes = False
        self.flood_once = False
        self.context_bot = Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN")

    async def get_my_commands(self, *, scope, language_code):
        key = scope_key(scope), language_code
        self.calls.append(("get", key))
        if self.flood_once:
            self.flood_once = False
            raise TelegramRetryAfter(
                method=GetMyCommands(), message="Too Many Requests", retry_after=1
            )
        if scope_key(scope) in self.unavailable:
            raise TelegramBadRequest(method=GetMyCommands(), message="Bad Request: chat not found")
        return [
            BotCommand.model_validate(command.model_dump(), context={"bot": self.context_bot})
            for command in self.menus.get(key, [])
        ]

    async def delete_my_commands(self, *, scope, language_code):
        key = scope_key(scope), language_code
        self.calls.append(("delete", key))
        if not self.drop_writes:
            self.menus.pop(key, None)
        return True

    async def set_my_commands(self, *, commands, scope, language_code):
        key = scope_key(scope), language_code
        self.calls.append(("set", key))
        if self.fail_registration:
            self.fail_registration = False
            raise TelegramBadRequest(
                method=GetMyCommands(), message="Bad Request: temporary failure"
            )
        if not self.drop_writes:
            self.menus[key] = commands
        return True


def legacy():
    return [
        BotCommand(command=name, description="Old command")
        for name in ("all", "calc", "add_track", "remove_track", "group")
    ]


def test_public_menu_registry_matches_real_commands_and_localization():
    import inspect

    from setlist_bot.transport.controller import Controller

    source = inspect.getsource(Controller.execute)
    for name in DESCRIPTIONS:
        assert f'"{name}"' in source
        assert re.fullmatch(r"[a-z_]{1,32}", name)
    assert set(DESCRIPTIONS) == {name for names in MENUS.values() for name in names}
    for menu in MENUS:
        for language in ("", "ru", "en"):
            commands = menu_commands(menu, language)
            assert 1 <= len(commands) <= 100
            assert len(commands) == len({c.command for c in commands})
            assert all(1 <= len(c.description) <= 256 for c in commands)
            assert not {
                "all",
                "calc",
                "add_track",
                "remove_track",
                "confirm",
                "copy_list",
                "edit_list",
                "change_shared",
            } & {c.command for c in commands}
    assert MENUS["private"] == (
        "support",
        "groups",
        "concerts",
        "my_gigs",
        "catalog",
        "profile",
        "help",
    )
    assert MENUS["group"] == ("support", "concerts", "catalog", "register", "help", "new_band")
    assert MENUS["admin"] == MENUS["group"] + ("add_new_songs", "concert")
    assert (
        menu_commands("private", "")[0].description == menu_commands("private", "ru")[0].description
    )
    assert (
        menu_commands("private", "ru")[0].description
        != menu_commands("private", "en")[0].description
    )


async def test_sync_clears_legacy_scopes_languages_and_is_idempotent():
    chat = BotCommandScopeChat(chat_id=-100)
    member = BotCommandScopeChatMember(chat_id=-100, user_id=1)
    targets = CommandTargets(
        tuple(s for s, _ in GLOBAL_SCOPES) + (chat, member), ("", "ru", "en", "de")
    )
    bot = FakeCommandAPI()
    for scope in targets.scopes:
        for language in targets.languages:
            bot.menus[scope_key(scope), language] = legacy()
    for scope, menu in GLOBAL_SCOPES:
        if menu == "private":
            for language in ("", "ru", "en"):
                bot.menus[scope_key(scope), language] = menu_commands(menu, language) + [
                    BotCommand(command="calendar", description="Calendar")
                ]
    report = await synchronize_commands(bot, targets)
    assert report.checked == report.removed == 24 and report.registered == 12
    for scope, menu in GLOBAL_SCOPES:
        for language in ("", "ru", "en"):
            assert bot.menus[scope_key(scope), language] == menu_commands(menu, language)
        assert not bot.menus.get((scope_key(scope), "de"))
    assert not any(bot.menus.get((scope_key(chat), language)) for language in targets.languages)
    assert not any(bot.menus.get((scope_key(member), language)) for language in targets.languages)
    bot.calls.clear()
    report = await synchronize_commands(bot, targets)
    assert report.removed == report.registered == 0
    assert all(method == "get" for method, _ in bot.calls)


async def test_partial_failure_is_repaired_on_next_start():
    targets = CommandTargets(tuple(s for s, _ in GLOBAL_SCOPES), ("", "ru", "en"))
    bot = FakeCommandAPI()
    bot.menus[scope_key(GLOBAL_SCOPES[0][0]), ""] = legacy()
    bot.fail_registration = True
    with pytest.raises(TelegramBadRequest):
        await synchronize_commands(bot, targets)
    assert any(method == "delete" for method, _ in bot.calls)
    assert (await synchronize_commands(bot, targets)).registered == 12


async def test_real_api_bot_binding_does_not_trigger_rewrites():
    bot = FakeCommandAPI()
    targets = CommandTargets(tuple(s for s, _ in GLOBAL_SCOPES), ("", "ru", "en"))
    for scope, menu in GLOBAL_SCOPES:
        for language in targets.languages:
            bot.menus[scope_key(scope), language] = menu_commands(menu, language)
    scope = GLOBAL_SCOPES[0][0]
    actual = await bot.get_my_commands(scope=scope, language_code="ru")
    expected = menu_commands("default", "ru")
    assert actual != expected and actual[0].bot is bot.context_bot
    report = await synchronize_commands(bot, targets)
    assert report.checked == 12 and report.removed == report.registered == 0
    assert all(method == "get" for method, _ in bot.calls)


async def test_verification_detects_ignored_writes_and_honors_retry_after(monkeypatch):
    targets = CommandTargets(tuple(s for s, _ in GLOBAL_SCOPES), ("", "ru", "en"))
    bot = FakeCommandAPI()
    bot.drop_writes = True
    with pytest.raises(CommandSyncError, match="verification"):
        await synchronize_commands(bot, targets)
    bot.drop_writes = False
    bot.flood_once = True
    sleep = AsyncMock()
    monkeypatch.setattr("setlist_bot.transport.commands.asyncio.sleep", sleep)
    assert (await synchronize_commands(bot, targets)).registered == 12
    sleep.assert_awaited_once_with(1)


async def test_inaccessible_chat_does_not_block_global_sync_but_global_error_does():
    chat = BotCommandScopeChat(chat_id=-999)
    targets = CommandTargets(tuple(s for s, _ in GLOBAL_SCOPES) + (chat,), ("", "ru", "en"))
    bot = FakeCommandAPI()
    bot.unavailable.add(scope_key(chat))
    report = await synchronize_commands(bot, targets)
    assert report.unavailable == 3 and report.registered == 12
    bot.unavailable.add(scope_key(GLOBAL_SCOPES[0][0]))
    with pytest.raises(TelegramBadRequest):
        await synchronize_commands(bot, targets)


async def test_targets_include_known_private_group_admin_and_member_scopes(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(Context(user_id=world.admin.id, chat_id=1))
        group = await uow.store.get(Group, world.other.id)
        group.deleted_chat_id = -300
    extra = BotCommandScopeChat(chat_id=-400)
    targets = await collect_command_targets(
        engine, extra_scopes=[extra, extra], legacy_languages=["de"]
    )
    keys = [scope_key(scope) for scope in targets.scopes]
    assert len(keys) == len(set(keys))
    assert scope_key(BotCommandScopeChat(chat_id=1)) in keys
    assert scope_key(BotCommandScopeChat(chat_id=-100)) in keys
    assert (
        scope_key(BotCommandScopeChatMember(chat_id=-100, user_id=world.member.telegram_id)) in keys
    )
    assert scope_key(extra) in keys and "de" in targets.languages
    assert {"", "ru", "en"} <= set(targets.languages)


def test_historical_scope_configuration_validates_json_and_languages():
    settings = Settings(
        database_url="postgresql+asyncpg://test",
        bot_command_legacy_languages=["de"],
        bot_command_extra_scopes=[{"type": "chat_member", "chat_id": -100, "user_id": 1}],
    )
    assert settings.bot_command_extra_scopes[0].type == "chat_member"
    with pytest.raises(ValueError):
        Settings(database_url="postgresql+asyncpg://test", bot_command_legacy_languages=["RU"])


async def test_sync_only_runs_menu_sync_without_starting_polling(monkeypatch, database_url, engine):
    fake = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=123456, username="test_bot"))
    )

    class FakeBot:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(main, "Bot", FakeBot)
    monkeypatch.setattr(
        main, "Settings", lambda: Settings(database_url=database_url, bot_token="test")
    )
    synchronize = AsyncMock(
        return_value=SimpleNamespace(checked=12, removed=1, registered=12, unavailable=0)
    )
    monkeypatch.setattr(main, "synchronize_commands", synchronize)
    dispatcher = AsyncMock(side_effect=AssertionError("sync-only must never poll"))
    monkeypatch.setattr(main, "Dispatcher", dispatcher)
    await main.run(sync_only=True)
    synchronize.assert_awaited_once()
    dispatcher.assert_not_called()


@pytest.mark.parametrize("phase", ["read", "delete", "verify"])
async def test_chat_migration_redirects_scope_in_each_sync_phase(phase):
    old, new = -123, -100123

    class Migrating(FakeCommandAPI):
        def __init__(self):
            super().__init__()
            self.reads = 0
            self.triggered = False

        def redirect(self, scope):
            if getattr(scope, "chat_id", None) == old:
                self.triggered = True
                raise TelegramMigrateToChat(
                    method=GetMyCommands(), message="group upgraded", migrate_to_chat_id=new
                )

        async def get_my_commands(self, *, scope, language_code):
            if getattr(scope, "chat_id", None) == old:
                self.reads += 1
                if phase == "read" or phase == "verify" and self.reads == 2:
                    self.redirect(scope)
            return await super().get_my_commands(scope=scope, language_code=language_code)

        async def delete_my_commands(self, *, scope, language_code):
            if phase == "delete":
                self.redirect(scope)
            return await super().delete_my_commands(scope=scope, language_code=language_code)

    bot = Migrating()
    scope = BotCommandScopeChat(chat_id=old)
    bot.menus[scope_key(scope), ""] = legacy()
    bot.menus[scope_key(BotCommandScopeChat(chat_id=new)), ""] = legacy()
    migrated = AsyncMock()
    report = await synchronize_commands(
        bot, CommandTargets((scope,), ("",)), on_chat_migrated=migrated
    )
    assert bot.triggered
    migrated.assert_awaited_once_with(old, new)
    assert not bot.menus.get((scope_key(BotCommandScopeChat(chat_id=new)), ""))
    assert report.checked == 1 and report.unavailable == 0


async def test_migration_preserves_admin_member_scopes_languages_and_persisted_binding(
    engine, world
):
    from functools import partial

    from setlist_bot.application.chat_migration import migrate_binding
    from setlist_bot.domain.entities import ChatBinding

    old, new = -100, -100999

    class Migrating(FakeCommandAPI):
        async def get_my_commands(self, *, scope, language_code):
            if getattr(scope, "chat_id", None) == old:
                raise TelegramMigrateToChat(
                    method=GetMyCommands(), message="group upgraded", migrate_to_chat_id=new
                )
            return await super().get_my_commands(scope=scope, language_code=language_code)

    bot = Migrating()
    old_scopes = (
        BotCommandScopeChat(chat_id=old),
        BotCommandScopeChatAdministrators(chat_id=old),
        BotCommandScopeChatMember(chat_id=old, user_id=2),
    )
    new_scopes = tuple(scope.model_copy(update={"chat_id": new}) for scope in old_scopes)
    for scope in new_scopes:
        for lang in ("", "ru", "en"):
            bot.menus[scope_key(scope), lang] = legacy()
    targets = CommandTargets(
        tuple(scope for scope, _ in GLOBAL_SCOPES) + old_scopes + new_scopes, ("", "ru", "en")
    )
    report = await synchronize_commands(
        bot, targets, on_chat_migrated=partial(migrate_binding, engine)
    )
    assert report.checked == 21 and report.removed == 9 and report.registered == 12
    assert len([key for operation, key in bot.calls if operation == "delete"]) == 9
    for scope in new_scopes:
        assert all(not bot.menus.get((scope_key(scope), lang)) for lang in targets.languages)
    async with PostgresUnitOfWork(engine) as uow:
        binding = (await uow.store.find(ChatBinding, group_id=world.group.id))[0]
        assert binding.chat_id == new
        assert (await uow.store.find(ChatBinding, group_id=world.other.id))[0].chat_id == -200
        assert (await uow.store.get(Group, world.group.id)).owner_id == world.admin.id


@pytest.mark.parametrize("phase", ["delete", "verify"])
async def test_chat_becomes_inaccessible_after_initial_menu_read(phase):
    scope = BotCommandScopeChat(chat_id=-321)

    class Disappearing(FakeCommandAPI):
        async def delete_my_commands(self, *, scope, language_code):
            self.unavailable.add(scope_key(scope))
            if phase == "delete":
                raise TelegramBadRequest(method=GetMyCommands(), message="chat not found")
            return await super().delete_my_commands(scope=scope, language_code=language_code)

    bot = Disappearing()
    bot.menus[scope_key(scope), ""] = legacy()
    report = await synchronize_commands(bot, CommandTargets((scope,), ("",)))
    assert report.unavailable == 1


async def test_global_migration_and_cyclic_chat_redirects_still_fail():
    class Unexpected(FakeCommandAPI):
        async def get_my_commands(self, *, scope, language_code):
            raise TelegramMigrateToChat(
                method=GetMyCommands(), message="migration", migrate_to_chat_id=-100999
            )

    bot = Unexpected()
    with pytest.raises(TelegramMigrateToChat):
        await synchronize_commands(bot, CommandTargets((GLOBAL_SCOPES[0][0],), ("",)))

    class Cycle(FakeCommandAPI):
        async def get_my_commands(self, *, scope, language_code):
            raise TelegramMigrateToChat(
                method=GetMyCommands(),
                message="migration",
                migrate_to_chat_id=-100999 if scope.chat_id == -100 else -100,
            )

    with pytest.raises(CommandSyncError, match="Cyclic"):
        await synchronize_commands(
            Cycle(), CommandTargets((BotCommandScopeChat(chat_id=-100),), ("",))
        )


@pytest.mark.parametrize("sync_only", [True, False])
async def test_failed_startup_cools_down_but_explicit_sync_cli_exits_promptly(
    monkeypatch, database_url, engine, sync_only
):
    fake = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=123456, username="test_bot"))
    )

    class FakeBot:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(main, "Bot", FakeBot)
    monkeypatch.setattr(
        main, "Settings", lambda: Settings(database_url=database_url, bot_token="test")
    )
    monkeypatch.setattr(
        main,
        "synchronize_commands",
        AsyncMock(side_effect=RuntimeError("synthetic startup failure")),
    )
    sleep, dispatcher = AsyncMock(), AsyncMock()
    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    monkeypatch.setattr(main, "Dispatcher", dispatcher)
    with pytest.raises(SystemExit, match="Command menu synchronization failed"):
        await main.run(sync_only=sync_only)
    if sync_only:
        sleep.assert_not_awaited()
    else:
        sleep.assert_awaited_once_with(30)
    dispatcher.assert_not_called()
