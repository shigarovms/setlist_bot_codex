import logging
from collections.abc import AsyncIterator

from aiogram import Bot, F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.domain.entities import ProcessedUpdate, SetlistMessage, SongListTemplate, User
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
    ) -> Response | None:
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
            try:
                async with uow.session.begin_nested():
                    references = (
                        await uow.store.find(
                            SetlistMessage, chat_id=chat_id, message_id=reply_message_id
                        )
                        if reply_message_id is not None
                        else []
                    )
                    if (
                        reply_message_id is not None
                        and not command.lstrip().startswith("/")
                        and not references
                    ):
                        return None
                    response = await controller.execute(
                        command,
                        reply_template_id=references[0].template_id if references else None,
                    )
                    await uow.store.flush()
            except (AppError, ValueError, IndexError) as exc:
                code = exc.code if isinstance(exc, AppError) else "input"
                await uow.session.refresh(user)
                response = Response(tr(controller.language, code))
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
                    except AppError:
                        pass
            return response

    async def remember_response(
        self, chat_id: int, response: Response, messages: list[Message]
    ) -> None:
        if response.suggestion_template_id is None:
            return
        async with PostgresUnitOfWork(self.engine) as uow:
            templates = await uow.store.find(SongListTemplate, id=response.suggestion_template_id)
            # Cleanup may expire a template between sending and recording its message.
            if not templates:
                return
            for message in messages:
                if not await uow.store.find(
                    SetlistMessage, chat_id=chat_id, message_id=message.message_id
                ):
                    uow.store.add(
                        SetlistMessage(
                            template_id=response.suggestion_template_id,
                            chat_id=chat_id,
                            message_id=message.message_id,
                        )
                    )


async def keyboards(buttons: list[tuple[str, str]]) -> AsyncIterator[InlineKeyboardMarkup]:
    for start in range(0, len(buttons), 40):
        yield InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label[:100], callback_data=data)]
                for label, data in buttons[start : start + 40]
            ]
        )


async def send_response(bot: Bot, chat_id: int, response: Response) -> list[Message]:
    pieces = chunks(response.text)
    markup_batches = [markup async for markup in keyboards(response.buttons)]
    messages = []
    for i, piece in enumerate(pieces):
        markup = markup_batches.pop(0) if i == len(pieces) - 1 and markup_batches else None
        entities = (
            [MessageEntity(type="pre", offset=0, length=len(piece.encode("utf-16-le")) // 2)]
            if response.monospace
            else None
        )
        messages.append(
            await bot.send_message(
                chat_id,
                piece,
                entities=entities,
                parse_mode=None,
                reply_markup=ForceReply() if response.force_reply else markup,
            )
        )
    for markup in markup_batches:
        await bot.send_message(chat_id, "…", reply_markup=markup)
    for filename, data in response.files:
        await bot.send_document(chat_id, BufferedInputFile(data, filename=filename))
    return messages


def make_router(processor: Processor, username: str) -> Router:
    router = Router()

    @router.message(F.text)
    async def command(message: Message, event_update: Update, bot: Bot) -> None:
        if not message.from_user or message.from_user.is_bot or not message.text:
            return
        if not message.text.strip():
            return
        head = message.text.split()[0]
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
        if not message.text.lstrip().startswith("/") and reply_id is None:
            return
        private = message.chat.type == "private"
        if message.chat.type not in ("private", "group", "supergroup"):
            return
        chat_admin = False
        if head.split("@", 1)[0].casefold() == "/group" and not private:
            administrators = await bot.get_chat_administrators(message.chat.id)
            chat_admin = any(member.user.id == message.from_user.id for member in administrators)
        response = await processor.process(
            event_update.update_id,
            message.from_user.id,
            message.chat.id,
            message.text,
            private=private,
            chat_admin=chat_admin,
            display_name=message.from_user.full_name,
            reply_message_id=reply_id,
        )
        if response:
            sent = await send_response(bot, message.chat.id, response)
            await processor.remember_response(message.chat.id, response, sent)

    @router.callback_query(F.data)
    async def callback(query: CallbackQuery, event_update: Update, bot: Bot) -> None:
        if not isinstance(query.message, Message) or not query.data:
            await query.answer()
            return
        await query.answer()
        response = await processor.process(
            event_update.update_id,
            query.from_user.id,
            query.message.chat.id,
            query.data,
            private=query.message.chat.type == "private",
            display_name=query.from_user.full_name,
        )
        if response:
            sent = await send_response(bot, query.message.chat.id, response)
            await processor.remember_response(query.message.chat.id, response, sent)

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
