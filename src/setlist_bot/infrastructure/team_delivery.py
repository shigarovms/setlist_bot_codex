"""Deliver current team invitations with recipient and lifecycle checks."""

from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from setlist_bot.application.team import Team
from setlist_bot.domain.entities import Group, Membership, Outbox, User, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.team import invitation
from setlist_bot.transport.telegram import send_response


async def deliver_team(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> bool:
    member = None
    try:
        member = await uow.store.get(Membership, UUID(event.payload["member_id"]))
        group = await uow.store.get(Group, member.group_id)
        require(event.group_id == group.id and not group.deleted_at, "input_expired")
        person = await uow.store.get(
            User,
            UUID(event.payload["recipient_id"])
            if event.support_kind == "team_note"
            else member.user_id,
        )
        require(person.telegram_id == event.chat_id, "forbidden")
        c = Controller(uow.store, person, person.telegram_id, private=True)
        if event.support_kind == "team":
            require(member.version == event.payload["version"], "input_expired")
            response = await invitation(c, member)
        else:
            require(group.lifecycle_version == event.payload["epoch"], "input_expired")
            response = Response(
                group.name
                + "\n\n"
                + c.t(event.text, name=await c.groups.name(group.id, member.user_id))
            )
        await send_response(bot, event.chat_id, response)
        event.sent, event.delivered_at = True, now()
    except AppError:
        event.sent, event.discarded_at = True, now()
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        if isinstance(exc, TelegramBadRequest) and not any(
            s in exc.message.lower() for s in ("chat not found", "user not found")
        ):
            raise
        event.sent, event.discarded_at, event.last_error = True, now(), type(exc).__name__
        if member and member.invitation and event.support_kind == "team":
            actor = await uow.store.get(User, UUID(member.invitation["sender_id"]))
            await Team(uow.store, actor).notice(member, "team_failed", recipient=actor.id)
    event.attempts += 1
    return event.delivered_at is not None
