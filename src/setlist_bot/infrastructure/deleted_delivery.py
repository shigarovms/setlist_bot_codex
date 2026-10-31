"""Only replace buttons; never resend a deleted performance's setlist or private draft."""

import json
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    Group,
    Membership,
    Outbox,
    User,
    now,
)
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.render import deleted_buttons


async def deliver_retired_card(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> None:
    event.sent = True
    if event.group_id is None or event.concert_id is None:
        return
    group = await uow.store.get(Group, event.group_id)
    concert = await uow.store.get(Concert, event.concert_id)
    cards = await uow.store.find(
        Confirmation, id=UUID(event.payload["card_id"]), chat_id=event.chat_id
    )
    if (
        not cards
        or group.deleted_at is not None
        or concert.deleted_at is None
        or concert.lifecycle_version != event.payload.get("concert_epoch")
    ):
        return
    data = json.loads(cards[0].command)
    if (
        data.get("group_id") != str(group.id)
        or data.get("concert_id") != str(concert.id)
        or data.get("retired_epoch") != concert.lifecycle_version
        or not data.get("message_ids")
    ):
        return
    private = bool(data.get("private_chat"))
    language = group.language
    if private:
        if not data.get("owner_id"):
            return
        owner = await uow.store.get(User, UUID(data["owner_id"]))
        if owner.telegram_id != event.chat_id or not await uow.store.find(
            Membership, group_id=group.id, user_id=owner.id, status="active"
        ):
            return
        language = owner.language
    elif not await uow.store.find(ChatBinding, group_id=group.id, chat_id=event.chat_id):
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action)]
            for label, action in deleted_buttons(group.id, language, private)
        ]
    )
    try:
        await bot.edit_message_reply_markup(
            chat_id=event.chat_id, message_id=data["message_ids"][-1], reply_markup=markup
        )
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if not any(
            marker in message
            for marker in (
                "not modified",
                "message to edit not found",
                "can't be edited",
                "message_id_invalid",
            )
        ):
            raise
    event.delivered_at = now()
