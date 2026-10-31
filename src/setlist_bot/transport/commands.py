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
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
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
    "reminders": ("Настроить напоминания о сет-листах", "Setlist reminders"),
    "start": ("Начать работу с ботом", "Start using the bot"),
    "support": ("Связаться с поддержкой", "Contact support"),
    "help": ("Команды и примеры использования", "Commands and examples"),
    "groups": ("Выбрать музыкальный коллектив", "Select a musical group"),
    "register": (
        "Имя в коллективе и заявка на вступление",
        "Group display name and membership application",
    ),
    "catalog": ("Репертуар и поиск песен", "Repertoire and song search"),
    "archived": ("Архивные песни", "Archived songs"),
    "concerts": ("Выбрать выступление", "Select a concert"),
    "calendar": ("Подписка на календарь выступлений", "Subscribe to performance calendars"),
    "all_my_concerts": (
        "Все мои выступления во всех коллективах",
        "My performances across all groups",
    ),
    "setlist": ("Сет-лист выбранного выступления", "Setlist for the selected concert"),
    "shared": ("Показать общий сет-лист", "Show the shared setlist"),
    "export": ("PDF утверждённого сет-листа", "Approved setlist PDFs"),
    "add": ("Добавить песни в сет-лист", "Add songs to the setlist"),
    "suggestion": (
        "Создать свой вариант из списка песен",
        "Create your variant from a list of songs",
    ),
    "versions": ("Варианты сет-листа и история", "Setlist variants and history"),
    "history": ("История изменений сет-листа", "Setlist change history"),
    "pause": ("Настроить среднюю паузу между песнями", "Set the average pause between songs"),
    "move": ("Переставить: /move откуда куда", "Reorder: /move from to"),
    "remove": ("Убрать песню: /remove позиция", "Remove a song: /remove position"),
    "add_new_songs": (
        "Добавить новые песни или вставить таблицу",
        "Add new songs or paste a table",
    ),
    "edit": ("Изменить песню или предложить правку", "Edit a song or propose a change"),
    "copy": ("Создать независимую копию песни", "Create an independent song copy"),
    "alias": ("Добавить альтернативное название песни", "Add an alternative song title"),
    "archive": ("Архивировать песню (админ)", "Archive a song (admin)"),
    "unarchive": ("Восстановить песню из архива (админ)", "Restore an archived song (admin)"),
    "publish": ("Предложить личный черновик группе", "Publish your personal draft to the group"),
    "language": ("Личный язык интерфейса: ru или en", "Personal interface language: ru or en"),
    "cancel": ("Отменить ожидающие подтверждения", "Cancel pending confirmations"),
    "group": ("Создать новый коллектив", "Create a new musical group"),
    "members": (
        "Заявки и участники коллектива (админ)",
        "Membership applications and members (admin)",
    ),
    "proposals": ("Предложения по каталогу (админ)", "Catalog proposals (admin)"),
    "concert": (
        "Создать новое выступление (админ)",
        "Create a concert: date  title (admin)",
    ),
    "approve": ("Утвердить общий сет-лист (админ)", "Approve the shared setlist (admin)"),
    "reopen": ("Вернуть сет-лист в работу (админ)", "Reopen the shared setlist (admin)"),
    "group_language": ("Язык коллектива: ru или en (админ)", "Group language: ru or en (admin)"),
    "group_pause": (
        "Пауза для новых выступлений (админ)",
        "Default pause for new concerts (admin)",
    ),
    "backup": ("Скачать общие данные ZIP (админ)", "Download shared data as ZIP (admin)"),
    "delete_concert": (
        "Удалить выбранное выступление (админ)",
        "Delete the selected concert (admin)",
    ),
    "delete_group": (
        "Удалить выбранный коллектив (админ)",
        "Delete the selected musical group (admin)",
    ),
    "trash": ("Корзина и восстановление (админ)", "Trash and restoration (admin)"),
}
COMMON = (
    "support",
    "groups",
    "concerts",
    "start",
    "add_new_songs",
    "help",
    "catalog",
    "setlist",
    "shared",
    "export",
    "add",
    "suggestion",
    "versions",
    "history",
    "pause",
    "move",
    "remove",
    "edit",
    "copy",
    "alias",
    "archived",
    "cancel",
)
ADMIN = (
    "reminders",
    "members",
    "proposals",
    "concert",
    "group_language",
    "group_pause",
    "archive",
    "unarchive",
    "backup",
    "delete_concert",
    "delete_group",
    "trash",
)
MENUS: dict[Menu, tuple[str, ...]] = {
    "default": COMMON[:6],
    "private": COMMON[:6]
    + ("all_my_concerts", "calendar")
    + COMMON[6:]
    + ("publish", "language")
    + ADMIN,
    "group": COMMON + ("register", "group"),
    "admin": COMMON + ("register", "group", "approve", "reopen") + ADMIN,
}
GLOBAL_SCOPES: tuple[tuple[BotCommandScopeUnion, Menu], ...] = (
    (BotCommandScopeDefault(), "default"),
    (BotCommandScopeAllPrivateChats(), "private"),
    (BotCommandScopeAllGroupChats(), "group"),
    (BotCommandScopeAllChatAdministrators(), "admin"),
)


def menu_commands(menu: Menu, language: str) -> list[BotCommand]:
    descriptions = 1 if language == "en" else 0
    result = [
        BotCommand(command=name, description=DESCRIPTIONS[name][descriptions])
        for name in MENUS[menu]
    ]
    if menu == "private":
        result = [
            BotCommand(
                command=c.command,
                description=(
                    "Мой черновик выбранного выступления"
                    if language != "en"
                    else "My draft for the selected concert"
                )
                if c.command == "setlist"
                else c.description,
            )
            for c in result
        ]
    return result


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


async def synchronize_commands(bot: Bot, targets: CommandTargets) -> CommandSyncReport:
    desired = {
        (scope_key(scope), language): menu_commands(menu, language)
        for scope, menu in GLOBAL_SCOPES
        for language in ("", "ru", "en")
    }
    accessible = []
    changed = []
    unavailable = 0
    for scope in targets.scopes:
        for language in targets.languages:
            try:
                current = await _request(
                    partial(bot.get_my_commands, scope=scope, language_code=language)
                )
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
                    unavailable += 1
                    continue
                raise
            expected = desired.get((scope_key(scope), language), [])
            accessible.append((scope, language, expected))
            if command_values(current) != command_values(expected):
                changed.append((scope, language, current, expected))
    removed = registered = 0
    for scope, language, current, _ in changed:
        if current:
            if not await _request(
                partial(bot.delete_my_commands, scope=scope, language_code=language)
            ):
                raise CommandSyncError("Telegram did not confirm command deletion")
            removed += 1
    for scope, language, _, expected in changed:
        if expected:
            if not await _request(
                partial(bot.set_my_commands, commands=expected, scope=scope, language_code=language)
            ):
                raise CommandSyncError("Telegram did not confirm command registration")
            registered += 1
    if changed:
        for scope, language, expected in accessible:
            actual = await _request(
                partial(bot.get_my_commands, scope=scope, language_code=language)
            )
            if command_values(actual) != command_values(expected):
                raise CommandSyncError("Telegram command verification failed")
    report = CommandSyncReport(len(accessible), removed, registered, unavailable)
    logger.info(
        "Command menus verified: checked=%s removed=%s registered=%s unavailable=%s",
        report.checked,
        report.removed,
        report.registered,
        report.unavailable,
    )
    return report
