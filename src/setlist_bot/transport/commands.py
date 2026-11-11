"""Authoritative public slash-command menus and Bot API reconciliation.

Telegram cannot enumerate all scope/language registrations. We manage global
menus, remove overrides for known peers, and accept explicit legacy targets.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeChatAdministrators,
    BotCommandScopeChatMember,
    BotCommandScopeDefault,
    BotCommandScopeUnion,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)
Menu = Literal["default", "private", "group", "admin"]

# Only user-facing commands belong here; callbacks and commands requiring opaque
# identifiers are intentionally excluded. Backend permissions remain authoritative.
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "support": ("Поддержка", "Support"),
    "groups": ("Мои коллективы", "My bands"),
    "concerts": ("Выступления коллектива", "Band performances"),
    "my_gigs": ("Моё расписание", "My schedule"),
    "catalog": ("Репертуар", "Repertoire"),
    "profile": ("Мой профиль", "My profile"),
    "help": ("Помощь", "Help"),
    "register": ("Подать заявку в коллектив", "Apply to join the band"),
    "new_band": ("Подключить коллектив", "Connect a band"),
    "add_new_songs": ("Добавить песни", "Add songs"),
    "concert": ("Создать выступление", "Create a performance"),
}
GROUP = ("support", "concerts", "catalog", "register", "help", "new_band")
MENUS: dict[Menu, tuple[str, ...]] = {
    "default": ("support", "help"),
    "private": (
        "support",
        "groups",
        "concerts",
        "my_gigs",
        "catalog",
        "profile",
        "help",
    ),
    "group": GROUP,
    "admin": GROUP + ("add_new_songs", "concert"),
}
GLOBAL_SCOPES: tuple[tuple[BotCommandScopeUnion, Menu], ...] = (
    (BotCommandScopeDefault(), "default"),
    (BotCommandScopeAllPrivateChats(), "private"),
    (BotCommandScopeAllGroupChats(), "group"),
    (BotCommandScopeAllChatAdministrators(), "admin"),
)


def menu_commands(menu: Menu, language: str) -> list[BotCommand]:
    descriptions = 1 if language == "en" else 0
    return [
        BotCommand(command=name, description=DESCRIPTIONS[name][descriptions])
        for name in MENUS[menu]
    ]


def scope_key(scope: BotCommandScopeUnion) -> str:
    return json.dumps(scope.model_dump(mode="json", exclude_none=True), sort_keys=True)


def command_values(commands: Sequence[BotCommand]) -> list[tuple[str, str]]:
    # API models carry a private Bot reference; equality must compare public data only.
    return [(command.command, command.description) for command in commands]


@dataclass(frozen=True)
class CommandTargets:
    scopes: tuple[BotCommandScopeUnion, ...]
    languages: tuple[str, ...]


async def collect_command_targets(
    engine: AsyncEngine,
    *,
    extra_scopes: Sequence[BotCommandScopeUnion] = (),
    legacy_languages: Sequence[str] = (),
) -> CommandTargets:
    async with engine.connect() as connection:
        contexts = (
            await connection.execute(
                text(
                    "SELECT c.chat_id,u.telegram_id FROM contexts c JOIN users u ON u.id=c.user_id"
                )
            )
        ).all()
        chats = set(
            (
                await connection.execute(
                    text(
                        "SELECT chat_id FROM support_settings UNION "
                        "SELECT chat_id FROM chat_bindings UNION "
                        "SELECT deleted_chat_id FROM groups "
                        "WHERE deleted_chat_id IS NOT NULL"
                    )
                )
            ).scalars()
        ) | {row[0] for row in contexts}
        member_rows = (
            await connection.execute(
                text(
                    "SELECT coalesce(b.chat_id,g.deleted_chat_id),u.telegram_id "
                    "FROM memberships m "
                    "JOIN users u ON u.id=m.user_id JOIN groups g ON g.id=m.group_id "
                    "LEFT JOIN chat_bindings b ON b.group_id=g.id "
                    "WHERE coalesce(b.chat_id,g.deleted_chat_id) IS NOT NULL"
                )
            )
        ).all()
        members: set[tuple[int, int]] = {(int(row[0]), int(row[1])) for row in member_rows}
        members.update((int(row[0]), int(row[1])) for row in contexts if row[0] < 0)
        languages = (
            set(
                (
                    await connection.execute(
                        text("SELECT language FROM users UNION SELECT language FROM groups")
                    )
                ).scalars()
            )
            | set(legacy_languages)
            | {"", "ru", "en"}
        )
    scopes = [scope for scope, _ in GLOBAL_SCOPES]
    for chat_id in sorted(chats):
        scopes.append(BotCommandScopeChat(chat_id=chat_id))
        if chat_id < 0:
            scopes.append(BotCommandScopeChatAdministrators(chat_id=chat_id))
    for chat_id, user_id in sorted(members):
        if chat_id < 0:
            scopes.append(BotCommandScopeChatMember(chat_id=chat_id, user_id=user_id))
    scopes.extend(extra_scopes)
    return CommandTargets(
        tuple({scope_key(scope): scope for scope in scopes}.values()),
        tuple(sorted(languages)),
    )


@dataclass(frozen=True)
class CommandSyncReport:
    checked: int
    removed: int
    registered: int
    unavailable: int


class CommandSyncError(RuntimeError):
    pass


async def _request[T](request: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(3):
        try:
            return await request()
        except TelegramRetryAfter as exc:
            if attempt == 2:
                raise
            logger.info("Command menu rate limit; retrying after %s seconds", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
    raise AssertionError("unreachable")


class _ScopedRequests:
    """RU: Переход группы в супергруппу не отменяет запуск бота.

    EN: Follow Telegram-provided redirects for every scope operation, not just reads.
    Keep member/admin scope and language intact; never reinterpret a global error.
    """

    def __init__(self, migrated: Callable[[int, int], Awaitable[None]] | None):
        self.migrated = migrated
        self.redirects: dict[int | str, int] = {}
        self.unavailable: set[tuple[str, str]] = set()

    def resolve(self, scope: BotCommandScopeUnion) -> BotCommandScopeUnion:
        visited: set[int | str] = set()
        while (
            isinstance(
                scope,
                (BotCommandScopeChat, BotCommandScopeChatAdministrators, BotCommandScopeChatMember),
            )
            and scope.chat_id in self.redirects
        ):
            if scope.chat_id in visited:
                raise CommandSyncError("Cyclic Telegram chat migration")
            visited.add(scope.chat_id)
            scope = scope.model_copy(update={"chat_id": self.redirects[scope.chat_id]})
        return scope

    async def call[T](
        self,
        scope: BotCommandScopeUnion,
        language: str,
        request: Callable[..., Awaitable[T]],
    ) -> tuple[BotCommandScopeUnion, T] | None:
        for _ in range(8):
            scope = self.resolve(scope)
            key = scope_key(scope), language
            if key in self.unavailable:
                return None
            try:
                return scope, await _request(partial(request, scope=scope))
            except TelegramMigrateToChat as exc:
                if not isinstance(
                    scope,
                    (
                        BotCommandScopeChat,
                        BotCommandScopeChatAdministrators,
                        BotCommandScopeChatMember,
                    ),
                ):
                    raise
                old, new = scope.chat_id, exc.migrate_to_chat_id
                if new >= 0 or old == new or isinstance(old, int) and old >= 0:
                    raise CommandSyncError("Invalid Telegram chat migration") from exc
                self.redirects[old] = new
                self.resolve(scope)  # Reject cycles before changing any persisted binding.
                if self.migrated is not None and isinstance(old, int):
                    await self.migrated(old, new)
                logger.info("Command scope redirected after Telegram chat migration")
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                if isinstance(
                    scope,
                    (
                        BotCommandScopeChat,
                        BotCommandScopeChatAdministrators,
                        BotCommandScopeChatMember,
                    ),
                ) and (
                    isinstance(exc, TelegramForbiddenError)
                    or any(
                        reason in exc.message.lower()
                        for reason in (
                            "chat not found",
                            "user not found",
                            "member not found",
                            "participant_id_invalid",
                            "not enough rights",
                            "bot is not a member",
                            "bot was kicked",
                        )
                    )
                ):
                    self.unavailable.add(key)
                    return None
                raise
        raise CommandSyncError("Too many Telegram chat migrations")


async def synchronize_commands(
    bot: Bot,
    targets: CommandTargets,
    *,
    on_chat_migrated: Callable[[int, int], Awaitable[None]] | None = None,
) -> CommandSyncReport:
    desired = {
        (scope_key(scope), language): menu_commands(menu, language)
        for scope, menu in GLOBAL_SCOPES
        for language in ("", "ru", "en")
    }
    requests = _ScopedRequests(on_chat_migrated)
    accessible = []
    changed = []
    visited = set()
    for scope in targets.scopes:
        for language in targets.languages:
            if (scope_key(requests.resolve(scope)), language) in visited:
                continue
            result = await requests.call(
                scope,
                language,
                partial(bot.get_my_commands, language_code=language),
            )
            if result is None:
                continue
            selected, current = result
            key = scope_key(selected), language
            if key in visited:
                continue
            visited.add(key)
            expected = desired.get(key, [])
            accessible.append((selected, language, expected))
            if command_values(current) != command_values(expected):
                changed.append((selected, language, current, expected))
    removed = registered = 0
    for scope, language, current, _ in changed:
        if current:
            written = await requests.call(
                scope,
                language,
                partial(bot.delete_my_commands, language_code=language),
            )
            if written is None:
                continue
            if not written[1]:
                raise CommandSyncError("Telegram did not confirm command deletion")
            removed += 1
    for scope, language, _, expected in changed:
        if expected:
            written = await requests.call(
                scope,
                language,
                partial(bot.set_my_commands, commands=expected, language_code=language),
            )
            if written is None:
                continue
            if not written[1]:
                raise CommandSyncError("Telegram did not confirm command registration")
            registered += 1
    if changed:
        for scope, language, expected in accessible:
            result = await requests.call(
                scope,
                language,
                partial(bot.get_my_commands, language_code=language),
            )
            if (
                result is not None
                and not expected
                and command_values(result[1])
                and scope_key(result[0]) != scope_key(scope)
            ):
                # RU: Перенос мог произойти после чтения/удаления старого меню.
                # EN: Reconcile the destination's override if migration happened mid-sync.
                written = await requests.call(
                    result[0], language, partial(bot.delete_my_commands, language_code=language)
                )
                if written is None:
                    continue
                if not written[1]:
                    raise CommandSyncError("Telegram did not confirm command deletion")
                removed += 1
                result = await requests.call(
                    result[0], language, partial(bot.get_my_commands, language_code=language)
                )
            if result is not None and command_values(result[1]) != command_values(expected):
                raise CommandSyncError("Telegram command verification failed")
    report = CommandSyncReport(len(accessible), removed, registered, len(requests.unavailable))
    logger.info(
        "Command menus verified: checked=%s removed=%s registered=%s unavailable=%s",
        report.checked,
        report.removed,
        report.registered,
        report.unavailable,
    )
    return report
