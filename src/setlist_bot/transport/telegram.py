import json
import logging
from asyncio import Lock
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from weakref import WeakValueDictionary

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Chat,
    ErrorEvent,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.application.chat_cleanup import message_ids as known_message_ids
from setlist_bot.application.command_inputs import CommandInputs
from setlist_bot.application.setlist_menus import CARD, SetlistMenus
from setlist_bot.application.song_selection import SongSelection
from setlist_bot.application.support import Support
from setlist_bot.domain.entities import (
    Confirmation,
    ProcessedUpdate,
    SetlistMessage,
    SongListTemplate,
    User,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.telemetry import Monitor
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.i18n import tr

logger = logging.getLogger(__name__)


def chunks(value: str, limit: int = 3500) -> list[str]:
    # Telegram counts UTF-16 code units, including astral characters such as emoji.
    result: list[str] = []
    current, size = "", 0
    for char in value:
        width = len(char.encode("utf-16-le")) // 2
        if size + width > limit:
            newline = current.rfind("\n")
            split = newline + 1 if newline >= len(current) // 2 else len(current)
            result.append(current[:split])
            current = current[split:]
            size = len(current.encode("utf-16-le")) // 2
        current += char
        size += width
    if current:
        result.append(current)
    return result


class Processor:
    def __init__(
        self, engine: AsyncEngine, *, bot_username: str = "", monitor: Monitor | None = None
    ) -> None:
        self.monitor = monitor
        self.engine = engine
        self.bot_username = bot_username

    async def process(
        self,
        update_id: int,
        telegram_id: int,
        chat_id: int,
        command: str,
        *,
        private: bool,
        chat_admin: bool = False,
        display_name: str = "",
        sender_username: str = "",
        reply_message_id: int | None = None,
        message_id: int | None = None,
        is_callback: bool | None = None,
    ) -> Response | None:
        # RU: Здесь меняем только состояние БД. Отправка Telegram выполняется после
        # завершения транзакции. Вложенная транзакция ниже откатывает неудачную правку,
        # сохраняя возможность вернуть понятное сообщение об ошибке пользователю.
        # EN: Persist state first; Telegram delivery runs after transaction completion.
        # The nested savepoint rolls back a failed use case before returning its error.
        if is_callback is None:
            # Preserve direct Processor callers that pass internal callback commands.
            is_callback = (
                message_id is None and reply_message_id is None and not command.startswith("/")
            )
        async with PostgresUnitOfWork(self.engine) as uow:
            # Serializes user creation and each chat's command stream; group locks additionally
            # serialize access from private chats with commands from the shared chat.
            await uow.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": telegram_id}
            )
            await uow.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": chat_id})
            if await uow.store.find(ProcessedUpdate, update_id=update_id):
                return None
            uow.store.add(ProcessedUpdate(update_id=update_id))
            if self.monitor:
                self.monitor.set("telegram", "updates_received")
            users = await uow.store.find(User, telegram_id=telegram_id)
            user = users[0] if users else User(telegram_id=telegram_id)
            if not users:
                uow.store.add(user)
            user.last_seen_at = now()
            if display_name:
                user.display_name = display_name[:200]
            await uow.store.flush()
            controller = Controller(
                uow.store,
                user,
                chat_id,
                private=private,
                chat_admin=chat_admin,
                bot_username=self.bot_username,
            )
            controller.sender_username = sender_username
            controller.source_message_id = message_id if is_callback else None
            response: Response | None
            before_dialogs = {
                row.id: json.loads(row.command)
                for row in await uow.store.find(Confirmation, user_id=user.id, chat_id=chat_id)
            }
            input_reply_id = None
            try:
                async with uow.session.begin_nested():
                    # RU: Ответ принимается на сохранённое сообщение в текущем чате поддержки.
                    # EN: Route only exact, persisted reply targets in the configured support chat.
                    if not is_callback and not command.lstrip().startswith("/"):
                        if reply_message_id is not None and not private:
                            if await Support(uow.store, user).reply(
                                chat_id, reply_message_id, command
                            ):
                                return Response(tr(user.language, "support_reply_sent"))
                        if private and reply_message_id is not None:
                            for session in await uow.store.find(
                                Confirmation, user_id=user.id, chat_id=chat_id
                            ):
                                data = json.loads(session.command)
                                if (
                                    data.get("kind") == "support"
                                    and data.get("prompt_id") == reply_message_id
                                ):
                                    if session.expires_at <= now():
                                        await uow.store.delete(session)
                                        return Response(
                                            tr(user.language, "input_expired"),
                                            delete_message_ids=[reply_message_id],
                                        )
                                    delivered = await Support(uow.store, user).submit(command)
                                    await uow.store.delete(session)
                                    return Response(
                                        tr(
                                            user.language,
                                            "support_sent" if delivered else "support_unavailable",
                                        ),
                                        delete_message_ids=[reply_message_id],
                                    )
                    selection_before = await controller.selection.active() if private else None
                    references = (
                        await uow.store.find(
                            SetlistMessage, chat_id=chat_id, message_id=reply_message_id
                        )
                        if reply_message_id is not None
                        else []
                    )
                    creation_reply = (
                        await controller.groups.creation_reply(chat_id, reply_message_id)
                        if not private
                        and not is_callback
                        and reply_message_id is not None
                        and not command.lstrip().startswith("/")
                        else None
                    )
                    input_reply = (
                        await controller.inputs.reply(reply_message_id)
                        if not is_callback and reply_message_id is not None
                        else None
                    )
                    if input_reply is not None and command.lstrip().startswith("/"):
                        parts = command.lstrip().split(maxsplit=1)
                        name = parts[0].lstrip("/").split("@", 1)[0].lower()
                        if len(parts) < 2 or name != CommandInputs.data(input_reply)["command"]:
                            input_reply = None
                    if (
                        (reply_message_id is not None or not private)
                        and not is_callback
                        and not command.lstrip().startswith("/")
                        and not references
                        and creation_reply is None
                        and input_reply is None
                    ):
                        return None
                    if input_reply is not None:
                        input_reply_id = input_reply.id
                        response = await controller.command_input(input_reply, command)
                    elif creation_reply is not None:
                        response = await controller.group_name_input(creation_reply, command)
                    elif (
                        private
                        and not is_callback
                        and not command.lstrip().startswith("/")
                        and not references
                    ):
                        response = await controller.selection_input(command, message_id)
                    else:
                        response = await controller.execute(
                            command,
                            reply_template_id=references[0].template_id if references else None,
                        )
                    if (
                        selection_before is not None
                        and response is not None
                        and response.selection_id is None
                        and command.split(maxsplit=1)[0]
                        not in ("input_cancel", "confirm_cancel", "import_cancel")
                        and not response.input_rejected
                        and (
                            is_callback
                            or command.lstrip().startswith("/")
                            or input_reply is not None
                        )
                    ):
                        await controller.selection.clear()
                        response.remove_keyboard = response.remove_keyboard or (
                            SongSelection.data(selection_before)["stage"] == "choosing"
                        )
                        if response.remove_keyboard and not response.keyboard_text:
                            response.keyboard_text = tr(
                                controller.language, "selection_keyboard_closed"
                            )
                    if response is not None:
                        after_dialogs = {
                            row.id: json.loads(row.command)
                            for row in await uow.store.find(
                                Confirmation, user_id=user.id, chat_id=chat_id
                            )
                        }
                        for key, old in before_dialogs.items():
                            if old.get("kind") == CARD:
                                continue
                            if (
                                key not in after_dialogs
                                or (response.force_reply and key == response.command_input_id)
                                or (
                                    old.get("kind") == "setlist_request"
                                    and old.get("status") == "pending"
                                    and after_dialogs.get(key, {}).get("status") != "pending"
                                )
                            ):
                                response.delete_message_ids.extend(
                                    known_message_ids(old) - set(response.edit_message_ids)
                                )
                    await uow.store.flush()
            except (AppError, ValueError, IndexError) as exc:
                code = exc.code if isinstance(exc, AppError) else "input"
                await uow.session.refresh(user)
                response = Response(tr(controller.language, code))
                if input_reply_id is not None:
                    sessions = await uow.store.find(
                        Confirmation, id=input_reply_id, user_id=user.id, chat_id=chat_id
                    )
                    terminal = {
                        "input_expired",
                        "conflict",
                        "forbidden",
                        "admin_only",
                        "chat_admin",
                        "group_only",
                        "private_only",
                        "in_trash",
                        "select_group",
                        "select_concert",
                        "already_member",
                    }
                    if sessions and code not in terminal:
                        data = CommandInputs.data(sessions[0])
                        sessions[0].expires_at = now() + timedelta(minutes=10)
                        response.text += "\n\n" + data["prompt"]
                        response.monospace = bool(data.get("monospace_body"))
                        response.monospace_body = data.get("monospace_body")
                        response.force_reply = True
                        response.command_input_id = sessions[0].id
                        response.cancel_action = f"input_cancel {sessions[0].id.hex}"
                        response.cancel_label = tr(controller.language, "cancel_button")
                        if private and data.get("command") == "picker_pause":
                            response.delete_input = True
                    elif sessions:
                        data = CommandInputs.data(sessions[0])
                        if private and data.get("command") == "picker_pause":
                            response.delete_message_ids.extend(
                                data.get("transient_message_ids", [])
                            )
                        await uow.store.delete(sessions[0])
                if is_callback and command.split(maxsplit=1)[0] in (
                    "slmenu",
                    "slact",
                    "slopt",
                    "slrev",
                    "slreq",
                    "slreturn",
                    "sldraft",
                    "slformat",
                    "export_settings",
                    "export_menu",
                    "export_back",
                    "export_file",
                ):
                    response.callback_notice = response.text
                    return response
                if code == "conflict":
                    try:
                        context = await controller.groups.context(chat_id, private)
                        if controller.reply_target is not None:
                            context.group_id, context.concert_id = controller.reply_target
                        if context.group_id and context.concert_id:
                            current = await controller._show(context)
                            response.text += "\n\n" + current.text
                            response.buttons = current.buttons
                            response.monospace = current.monospace
                            response.monospace_body = current.monospace_body
                            response.suggestion_template_id = current.suggestion_template_id
                            response.menu_card_id = current.menu_card_id
                    except AppError:
                        pass
            if response is not None:
                if response.force_reply and response.command_input_id in before_dialogs:
                    response.delete_message_ids.extend(
                        known_message_ids(before_dialogs[response.command_input_id])
                    )
                response.navigation_owner_id = user.id
            return response

    async def remember_response(
        self, chat_id: int, response: Response, messages: list[Message]
    ) -> None:
        if (
            response.support_prompt_id is None
            and response.suggestion_template_id is None
            and response.selection_id is None
            and response.group_creation_id is None
            and response.menu_card_id is None
            and response.command_input_id is None
            and not response.buttons
            and response.navigation_owner_id is None
        ):
            return
        async with PostgresUnitOfWork(self.engine) as uow:
            if response.navigation_owner_id is not None and messages:
                # RU: Храним только текущую навигационную панель, не историю сообщений.
                # EN: One active navigation session per user/chat, never a message-history journal.
                for old_panel in await uow.store.find(
                    Confirmation, user_id=response.navigation_owner_id, chat_id=chat_id
                ):
                    old_data = json.loads(old_panel.command)
                    if old_data.get("kind") == "navigation_panel":
                        response.delete_message_ids.extend(
                            known_message_ids(old_data) - {m.message_id for m in messages}
                        )
                        await uow.store.delete(old_panel)
                bound = (
                    response.force_reply
                    or response.menu_card_id
                    or response.selection_id
                    or response.group_creation_id
                    or response.suggestion_template_id
                    or any(
                        action.split()[0] in ("confirm", "position", "group_create_yes")
                        for _, action in response.buttons
                    )
                )
                if not bound:
                    uow.store.add(
                        Confirmation(
                            user_id=response.navigation_owner_id,
                            chat_id=chat_id,
                            expires_at=now() + timedelta(days=2),
                            command=json.dumps(
                                {
                                    "kind": "navigation_panel",
                                    "message_ids": [m.message_id for m in messages],
                                }
                            ),
                        )
                    )
            for _, action in response.buttons:
                parts = action.split()
                if len(parts) < 2 or parts[0] not in (
                    "confirm",
                    "confirm_cancel",
                    "position",
                    "group_create_yes",
                    "group_create_edit",
                ):
                    continue
                try:
                    from uuid import UUID

                    identifier = UUID(parts[1])
                except ValueError:
                    continue
                rows = await uow.store.find(Confirmation, id=identifier, chat_id=chat_id)
                if rows:
                    data = json.loads(rows[0].command)
                    rows[0].command = json.dumps(
                        {**data, "message_ids": [message.message_id for message in messages]}
                    )
            if response.support_prompt_id is not None and messages:
                rows = await uow.store.find(
                    Confirmation, id=response.support_prompt_id, chat_id=chat_id
                )
                if rows:
                    rows[0].command = json.dumps(
                        {"kind": "support", "prompt_id": messages[-1].message_id}
                    )
            if response.command_input_id is not None and messages:
                rows = await uow.store.find(
                    Confirmation, id=response.command_input_id, chat_id=chat_id
                )
                if rows:
                    data = CommandInputs.data(rows[0])
                    if data.get("kind") == "command_input":
                        data["prompt_id"] = messages[-1].message_id
                        if data.get("private") and data.get("command") == "picker_pause":
                            identifiers = [
                                *data.get("transient_message_ids", []),
                                *[m.message_id for m in messages],
                                *response.auxiliary_message_ids,
                            ]
                            if response.keyboard_message_id is not None:
                                identifiers.append(response.keyboard_message_id)
                            data["transient_message_ids"] = list(dict.fromkeys(identifiers))
                        CommandInputs.write(rows[0], data)
            if response.menu_card_id is not None and messages:
                cards = await uow.store.find(
                    Confirmation, id=response.menu_card_id, chat_id=chat_id
                )
                if cards:
                    data = SetlistMenus.data(cards[0])
                    if data.get("kind") == CARD:
                        for old_card in await uow.store.find(Confirmation, chat_id=chat_id):
                            old = json.loads(old_card.command)
                            if (
                                old_card.id != cards[0].id
                                and old.get("kind") == CARD
                                and not old.get("approved")
                                and not old.get("closed")
                                and old.get("group_id") == data["group_id"]
                                and old.get("concert_id") == data["concert_id"]
                                and old.get("personal") == data["personal"]
                                and old.get("owner_id") == data["owner_id"]
                                and old.get("version", 0) < data["version"]
                            ):
                                response.delete_message_ids.extend(
                                    known_message_ids(old) - {m.message_id for m in messages}
                                )
                                SetlistMenus.write(old_card, {**old, "closed": True})
                        SetlistMenus.write(
                            cards[0], {**data, "message_ids": [m.message_id for m in messages]}
                        )
            if response.group_creation_id is not None and messages:
                sessions = await uow.store.find(
                    Confirmation, id=response.group_creation_id, chat_id=chat_id
                )
                if sessions:
                    session = sessions[0]
                    data = json.loads(session.command)
                    if data.get("kind") == "create_group" and data.get("stage") == "name":
                        data["prompt_id"] = messages[-1].message_id
                        session.command = json.dumps(data, ensure_ascii=False)
            if response.selection_id is not None:
                sessions = await uow.store.find(
                    Confirmation,
                    id=response.selection_id,
                )
                if sessions:
                    session = sessions[0]
                    data = SongSelection.data(session)
                    if data["stage"] in ("choosing", "review"):
                        data["panel_ids"] = [message.message_id for message in messages]
                        if response.keyboard_message_id is not None:
                            data["keyboard_message_id"] = response.keyboard_message_id
                        SongSelection.write(session, data)
            if response.suggestion_template_id is None:
                for message in messages:
                    if message.message_id in response.edit_message_ids:
                        for binding in await uow.store.find(
                            SetlistMessage, chat_id=chat_id, message_id=message.message_id
                        ):
                            await uow.store.delete(binding)
                return
            templates = await uow.store.find(SongListTemplate, id=response.suggestion_template_id)
            # Cleanup may expire a template between sending and recording its message.
            if not templates:
                return
            for message in messages:
                bindings = await uow.store.find(
                    SetlistMessage, chat_id=chat_id, message_id=message.message_id
                )
                if bindings and message.message_id in response.edit_message_ids:
                    bindings[0].template_id = response.suggestion_template_id
                elif not bindings:
                    uow.store.add(
                        SetlistMessage(
                            template_id=response.suggestion_template_id,
                            chat_id=chat_id,
                            message_id=message.message_id,
                        )
                    )


async def keyboards(
    buttons: list[tuple[str, str]], columns: int = 1
) -> AsyncIterator[InlineKeyboardMarkup]:
    for start in range(0, len(buttons), 40):
        yield InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=label[:100], callback_data=data)
                    for label, data in buttons[i : i + columns]
                ]
                for i in range(start, min(start + 40, len(buttons)), columns)
            ]
        )


async def send_response(bot: Bot, chat_id: int, response: Response) -> list[Message]:
    if response.markup_only:
        batches = [markup async for markup in keyboards(response.buttons, response.button_columns)]
        if response.edit_message_ids:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=response.edit_message_ids[-1],
                    reply_markup=batches[0] if batches else None,
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in exc.message.lower():
                    raise
        for filename, data in response.files:
            await bot.send_document(chat_id, BufferedInputFile(data, filename=filename))
        return []
    if response.callback_notice and not response.buttons and not response.files:
        return []
    if response.force_reply and "/cancel" not in response.text:
        response.text += "\n\n/cancel"
    output_text = response.text
    body = response.monospace_body or response.text
    body_start = output_text.find(body) if response.monospace else -1
    if body_start > 0:
        prefix = output_text[:body_start].rstrip() + "\n\n"
        output_text = prefix + output_text[body_start:]
        body_start = len(prefix)
    # RU: Python считает символы, Telegram — UTF-16; ниже переводим границы каждого фрагмента.
    # EN: Python indexes code points; Telegram entities use UTF-16 offsets per message chunk.
    body_end = body_start + len(body)
    pieces = chunks(output_text)
    markup_batches = [
        markup async for markup in keyboards(response.buttons, response.button_columns)
    ]
    if response.reply_keyboard is not None or response.remove_keyboard:
        carrier = await bot.send_message(
            chat_id,
            response.keyboard_text or "⌨️",
            reply_markup=(
                ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text=label) for label in row]
                        for row in response.reply_keyboard
                    ],
                    resize_keyboard=True,
                    is_persistent=True,
                )
                if response.reply_keyboard is not None
                else ReplyKeyboardRemove()
            ),
        )
        response.keyboard_message_id = carrier.message_id
        if response.previous_keyboard_id is not None:
            await delete_selection_message(bot, chat_id, response.previous_keyboard_id)
    messages = []
    text_offset = 0
    for i, piece in enumerate(pieces):
        markup = markup_batches.pop(0) if i == len(pieces) - 1 and markup_batches else None
        start = max(0, body_start - text_offset)
        end = min(len(piece), body_end - text_offset)
        entities = None
        if response.monospace and body_start >= 0 and start < end:
            entities = [
                MessageEntity(
                    type="pre",
                    offset=len(piece[:start].encode("utf-16-le")) // 2,
                    length=len(piece[start:end].encode("utf-16-le")) // 2,
                )
            ]
        text_offset += len(piece)
        if i < len(response.edit_message_ids):
            identifier = response.edit_message_ids[i]
            try:
                edited = await bot.edit_message_text(
                    piece,
                    chat_id=chat_id,
                    message_id=identifier,
                    entities=entities,
                    parse_mode=None,
                    reply_markup=markup,
                )
                if isinstance(edited, Message):
                    messages.append(edited)
                else:
                    messages.append(
                        Message(
                            message_id=identifier,
                            date=datetime.now(UTC),
                            chat=Chat(id=chat_id, type="private"),
                        )
                    )
                continue
            except TelegramBadRequest as exc:
                if "message is not modified" in exc.message.lower():
                    messages.append(
                        Message(
                            message_id=identifier,
                            date=datetime.now(UTC),
                            chat=Chat(id=chat_id, type="private"),
                        )
                    )
                    continue
                # A manually deleted/expired panel is replaced and its new ID is persisted.
        messages.append(
            await bot.send_message(
                chat_id,
                piece,
                entities=entities,
                parse_mode=None,
                reply_markup=(
                    ForceReply() if response.force_reply and i == len(pieces) - 1 else markup
                ),
            )
        )
    for identifier in response.edit_message_ids[len(pieces) :]:
        await delete_selection_message(bot, chat_id, identifier)
    for markup in markup_batches:
        await bot.send_message(chat_id, "…", reply_markup=markup)
    for filename, data in response.files:
        await bot.send_document(chat_id, BufferedInputFile(data, filename=filename))
    for identifier in dict.fromkeys(response.delete_message_ids):
        await delete_selection_message(bot, chat_id, identifier)
    return messages


async def delete_selection_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as exc:
        logger.info("Could not delete selection message (%s)", type(exc).__name__)


def trace_message_route(message: Message, update_id: int, bot_id: int, username: str) -> None:
    """Log routing metadata only: no message content, user/chat IDs, names or tokens."""
    if message.chat.type not in ("group", "supergroup") and not (
        message.chat.type == "private" and message.forward_origin is not None
    ):
        return
    parts = (message.text or "").split(maxsplit=1)
    head = parts[0] if parts else ""
    addressed = head.startswith("/") and "@" in head
    address_matches = head.split("@", 1)[1].casefold() == username.casefold() if addressed else None
    command = next(
        (e for e in message.entities or [] if e.type == "bot_command" and e.offset == 0), None
    )
    head_length = len(head.encode("utf-16-le")) // 2
    name = head.partition("@")[0].casefold()
    command_name = name[1:] if name in ("/song", "/import", "/start", "/help") else "other"
    reply = message.reply_to_message
    logger.info(
        "Telegram message route: update=%s starts_command=%s addressed=%s address_matches=%s "
        "reply_to_bot=%s command_entity=%s sender_is_bot=%s sender_chat=%s "
        "private=%s forwarded=%s code_format=%s trailing_backslash=%s "
        "command_name=%s command_length=%s head_length=%s entity_covers_head=%s lines=%s spans=%s",
        update_id,
        (message.text or "").lstrip().startswith("/"),
        addressed,
        address_matches,
        bool(reply and reply.from_user and reply.from_user.id == bot_id),
        any(
            entity.type == "bot_command" and entity.offset == 0 for entity in message.entities or []
        ),
        bool(message.from_user and message.from_user.is_bot),
        bool(message.sender_chat),
        message.chat.type == "private",
        message.forward_origin is not None,
        any(entity.type in ("pre", "code") for entity in message.entities or []),
        head.endswith("\\"),
        command_name,
        command.length if command else 0,
        head_length,
        bool(command and command.length == head_length),
        (message.text or "").count("\n"),
        [(e.type, e.offset, e.length) for e in (message.entities or [])[:8]],
    )


def make_router(processor: Processor, username: str, *, diagnostics: bool = False) -> Router:
    router = Router()
    locks: WeakValueDictionary[int, Lock] = WeakValueDictionary()

    def delivery_lock(chat_id: int) -> Lock:
        lock = locks.get(chat_id)
        if lock is None:
            lock = Lock()
            locks[chat_id] = lock
        return lock

    @router.message(F.text)
    async def command(message: Message, event_update: Update, bot: Bot) -> None:
        if diagnostics:
            trace_message_route(message, event_update.update_id, bot.id, username)
        if not message.from_user or message.from_user.is_bot or not message.text:
            return
        if not message.text.strip() and message.reply_to_message is None:
            return
        parts = message.text.split(maxsplit=1)
        head = parts[0] if parts else ""
        if (
            head.startswith("/")
            and "@" in head
            and head.split("@", 1)[1].casefold() != username.casefold()
        ):
            return
        reply = message.reply_to_message
        reply_id = (
            reply.message_id if reply and reply.from_user and reply.from_user.id == bot.id else None
        )
        private = message.chat.type == "private"
        if not message.text.lstrip().startswith("/") and reply_id is None and not private:
            return
        if message.chat.type not in ("private", "group", "supergroup"):
            return
        chat_admin = False
        if head.split("@", 1)[0].casefold() == "/group" and not private:
            administrators = await bot.get_chat_administrators(message.chat.id)
            chat_admin = any(member.user.id == message.from_user.id for member in administrators)
        async with delivery_lock(message.chat.id):
            response = await processor.process(
                event_update.update_id,
                message.from_user.id,
                message.chat.id,
                message.text,
                private=private,
                chat_admin=chat_admin,
                display_name=message.from_user.full_name,
                sender_username=message.from_user.username or "",
                reply_message_id=reply_id,
                message_id=message.message_id,
                is_callback=False,
            )
            if response:
                if response.delete_input:
                    await delete_selection_message(bot, message.chat.id, message.message_id)
                previously_deleted = set(response.delete_message_ids)
                sent = await send_response(bot, message.chat.id, response)
                await processor.remember_response(message.chat.id, response, sent)
                for identifier in set(response.delete_message_ids) - previously_deleted:
                    await delete_selection_message(bot, message.chat.id, identifier)
                for target_chat, extra in response.deliveries:
                    extra_deleted = set(extra.delete_message_ids)
                    extra_sent = await send_response(bot, target_chat, extra)
                    await processor.remember_response(target_chat, extra, extra_sent)
                    for identifier in set(extra.delete_message_ids) - extra_deleted:
                        await delete_selection_message(bot, target_chat, identifier)

    @router.callback_query(F.data)
    async def callback(query: CallbackQuery, event_update: Update, bot: Bot) -> None:
        if not isinstance(query.message, Message) or not query.data:
            await query.answer()
            return
        async with delivery_lock(query.message.chat.id):
            chat_admin = False
            if query.message.chat.type in ("group", "supergroup") and query.data.split(maxsplit=1)[
                0
            ] in ("group_create_yes", "group_create_edit"):
                administrators = await bot.get_chat_administrators(query.message.chat.id)
                chat_admin = any(member.user.id == query.from_user.id for member in administrators)
            response = await processor.process(
                event_update.update_id,
                query.from_user.id,
                query.message.chat.id,
                query.data,
                private=query.message.chat.type == "private",
                display_name=query.from_user.full_name,
                is_callback=True,
                chat_admin=chat_admin,
                message_id=query.message.message_id,
            )
            if response:
                await query.answer(
                    text=response.callback_notice or None, show_alert=bool(response.callback_notice)
                )
            else:
                await query.answer()
            if response:
                previously_deleted = set(response.delete_message_ids)
                sent = await send_response(bot, query.message.chat.id, response)
                await processor.remember_response(query.message.chat.id, response, sent)
                for identifier in set(response.delete_message_ids) - previously_deleted:
                    await delete_selection_message(bot, query.message.chat.id, identifier)
                for target_chat, extra in response.deliveries:
                    extra_deleted = set(extra.delete_message_ids)
                    extra_sent = await send_response(bot, target_chat, extra)
                    await processor.remember_response(target_chat, extra, extra_sent)
                    for identifier in set(extra.delete_message_ids) - extra_deleted:
                        await delete_selection_message(bot, target_chat, identifier)

    @router.errors()
    async def failure(event: ErrorEvent, bot: Bot) -> bool:
        if processor.monitor:
            processor.monitor.set("telegram", "error", error=type(event.exception).__name__)
        # SQL exceptions may contain private payloads. Log only type and update ID.
        logger.error(
            "Update %s failed (%s)", event.update.update_id, type(event.exception).__name__
        )
        message = event.update.message
        if message is None and event.update.callback_query:
            callback_message = event.update.callback_query.message
            message = callback_message if isinstance(callback_message, Message) else None
        if message is not None:
            try:
                await bot.send_message(message.chat.id, tr("ru", "internal"))
            except Exception as exc:
                logger.warning("Could not report error (%s)", type(exc).__name__)
        return True

    return router
