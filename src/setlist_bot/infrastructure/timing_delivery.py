"""Recheck the audience of timetable notices before delivery."""

from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from setlist_bot.application.workspace_timing import participants, responsible, zone
from setlist_bot.domain.entities import Concert, Group, Outbox, User, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.performance_timing import timing_response
from setlist_bot.transport.telegram import send_response


async def deliver_timing(
    bot: Bot, uow: PostgresUnitOfWork, message: Outbox, group: Group | None, base_url: str
) -> bool:
    rows = await uow.store.find(Concert, id=message.concert_id, deleted_at=None)
    payload = message.payload
    if not group or group.deleted_at or not rows:
        message.sent, message.discarded_at = True, now()
        return False
    event = rows[0]
    person_id = UUID(payload["user"])
    warning = payload["kind"] == "warning"
    eligible = (
        await responsible(uow.store, group, event) == person_id
        if warning
        else person_id in await participants(uow.store, group, event)
    )
    if (
        event.group_id != group.id
        or event.date.isoformat() != payload["date"]
        or event.lifecycle_version != payload["epoch"]
        or not eligible
    ):
        message.sent, message.discarded_at = True, now()
        return False
    users = await uow.store.find(User, id=person_id, telegram_id=message.chat_id)
    if not users:
        message.sent, message.discarded_at = True, now()
        return False
    if warning:
        message.payload = {
            **payload,
            "rows": event.timing.get("rows", []),
            "zone": zone(event),
            "preliminary": event.timing.get("preliminary", False),
            "version": event.timing.get("version", 0),
        }
    response = timing_response(group, event, message, users[0].language, base_url)
    message.attempts += 1
    try:
        await send_response(bot, message.chat_id, response)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        message.sent, message.discarded_at = True, now()
        message.last_error = type(exc).__name__
        return False
    message.sent, message.delivered_at = True, now()
    return True
