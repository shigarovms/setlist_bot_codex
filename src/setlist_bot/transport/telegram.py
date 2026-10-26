import json
import logging
from asyncio import Lock
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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

from setlist_bot.application.setlist_menus import CARD, SetlistMenus
from setlist_bot.application.song_selection import SongSelection
from setlist_bot.domain.entities import (
    Confirmation,
    ProcessedUpdate,
    SetlistMessage,
    SongListTemplate,
    User,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
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
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

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
        reply_message_id: int | None = None,
        message_id: int | None = None,
        is_callback: bool | None = None,
    ) -> Response | None:
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
            users = await uow.store.find(User, telegram_id=telegram_id)
            user = users[0] if users else User(telegram_id=telegram_id)
            if not users:
                uow.store.add(user)
            if display_name:
                user.display_name = display_name[:200]
            await uow.store.flush()
            controller = Controller(
                uow.store, user, chat_id, private=private, chat_admin=chat_admin
            )
            controller.source_message_id = message_id if is_callback else None
            response: Response | None
            try:
                async with uow.session.begin_nested():
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
                    if (
                        (reply_message_id is not None or not private)
                        and not is_callback
                        and not command.lstrip().startswith("/")
                        and not references
                        and creation_reply is None
                    ):
                        return None
                    if creation_reply is not None:
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
                        and (is_callback or command.lstrip().startswith("/"))
                    ):
                        await controller.selection.clear()
                        response.remove_keyboard = response.remove_keyboard or (
                            SongSelection.data(selection_before)["stage"] == "choosing"
                        )
                        if response.remove_keyboard and not response.keyboard_text:
                            response.keyboard_text = tr(
                                controller.language, "selection_keyboard_closed"
                            )
                    await uow.store.flush()
            except (AppError, ValueError, IndexError) as exc:
                code = exc.code if isinstance(exc, AppError) else "input"
                await uow.session.refresh(user)
                response = Response(tr(controller.language, code))
                if is_callback and command.split(maxsplit=1)[0] in (
                    "slmenu",
                    "slact",
                    "slopt",
                    "slrev",
                    "slreq",
                    "slreturn",
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
                            response.suggestion_template_id = current.suggestion_template_id
                            response.menu_card_id = current.menu_card_id
                    except AppError:
                        pass
            return response

    async def remember_response(
        self, chat_id: int, response: Response, messages: list[Message]
    ) -> None:
        if (
            response.suggestion_template_id is None
            and response.selection_id is None
            and response.group_creation_id is None
            and response.menu_card_id is None
        ):
            return
        async with PostgresUnitOfWork(self.engine) as uow:
            if response.menu_card_id is not None and messages:
                cards = await uow.store.find(
                    Confirmation, id=response.menu_card_id, chat_id=chat_id
                )
                if cards:
                    data = SetlistMenus.data(cards[0])
                    if data.get("kind") == CARD:
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
    pieces = chunks(response.text)
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
    for i, piece in enumerate(pieces):
        markup = markup_batches.pop(0) if i == len(pieces) - 1 and markup_batches else None
        entities = (
            [MessageEntity(type="pre", offset=0, length=len(piece.encode("utf-16-le")) // 2)]
            if response.monospace
            else None
        )
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
                reply_markup=ForceReply() if response.force_reply else markup,
            )
        )
    for identifier in response.edit_message_ids[len(pieces) :]:
        await delete_selection_message(bot, chat_id, identifier)
    for markup in markup_batches:
        await bot.send_message(chat_id, "…", reply_markup=markup)
    for filename, data in response.files:
        await bot.send_document(chat_id, BufferedInputFile(data, filename=filename))
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
    reply = message.reply_to_message
    logger.info(
        "Telegram message route: update=%s starts_command=%s addressed=%s address_matches=%s "
        "reply_to_bot=%s command_entity=%s sender_is_bot=%s sender_chat=%s "
        "private=%s forwarded=%s code_format=%s trailing_backslash=%s",
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
                reply_message_id=reply_id,
                message_id=message.message_id,
                is_callback=False,
            )
            if response:
                if response.delete_input:
                    await delete_selection_message(bot, message.chat.id, message.message_id)
                sent = await send_response(bot, message.chat.id, response)
                await processor.remember_response(message.chat.id, response, sent)

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
                sent = await send_response(bot, query.message.chat.id, response)
                await processor.remember_response(query.message.chat.id, response, sent)
                for target_chat, extra in response.deliveries:
                    extra_sent = await send_response(bot, target_chat, extra)
                    await processor.remember_response(target_chat, extra, extra_sent)

    @router.errors()
    async def failure(event: ErrorEvent, bot: Bot) -> bool:
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
