"""RU: Перепроверка адресата перед отправкой. EN: Queue IDs, render current authorized state."""

from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import Concert, GigInvitation, Group, Outbox, User, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.staffing import invitation_view
from setlist_bot.transport.telegram import send_response


async def deliver_staffing(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> bool:
    row = None
    try:
        rows = await uow.store.find(GigInvitation, id=UUID(event.payload["invite_id"]))
        require(bool(rows), "input_expired")
        row = rows[0]
        person = await uow.store.get(User, UUID(event.payload["user_id"]))
        require(
            event.chat_id == person.telegram_id
            and event.group_id == row.group_id
            and event.concert_id == row.concert_id,
            "forbidden",
        )
        group = await uow.store.get(Group, row.group_id)
        concert = await uow.store.get(Concert, row.concert_id)
        service = Staffing(uow.store, person)
        kind = event.payload["kind"]
        c = Controller(uow.store, person, person.telegram_id, private=True)
        if event.payload.get("admin"):
            await service.groups.access(row.group_id, admin=True)
            require(not concert.deleted_at, "input_expired")
            body = await invitation_view(c, row.id)
        else:
            require(
                row.user_id == person.id and row.version == event.payload["version"],
                "input_expired",
            )
            if kind == "cancelled":
                require(row.status == "cancelled", "input_expired")
                body = Response(group.name, [(tr(person.language, "staff_my"), "my_gigs")])
            elif kind == "released":
                require(
                    row.status == "replaced" and not group.deleted_at and not concert.deleted_at,
                    "input_expired",
                )
                body = Response(group.name, [(tr(person.language, "staff_my"), "my_gigs")])
                if event.payload.get("target_id"):
                    targets = await uow.store.find(
                        GigInvitation, id=UUID(event.payload["target_id"]), user_id=person.id
                    )
                    if targets:
                        body.buttons.insert(
                            0,
                            (tr(person.language, "staff_details"), f"sf_view {targets[0].id.hex}"),
                        )
            else:
                require(service.valid(row, concert, group), "input_expired")
                body = await invitation_view(c, row.id)
        label = {
            "invitation": "invitation",
            "reply": "reply",
            "replacement": "replacement",
            "swapped": "swapped",
            "busy": "busy",
            "date_changed": "date_changed",
            "cancelled": "cancelled",
            "confirmed": "confirmed",
            "released": "released",
            "replacement_cancelled": "replacement_cancelled",
            "delivery_failed": "delivery_failed",
        }[kind]
        title = (
            tr(person.language, "staff_delivery_failed")
            if label == "delivery_failed"
            else tr(person.language, "staff_" + label + "_notice")
        )
        body.text = title + "\n\n" + body.text
        await send_response(bot, event.chat_id, body)
        event.sent, event.delivered_at = True, now()
        event.attempts += 1
        event.last_error = ""
    except AppError:
        event.sent, event.discarded_at = True, now()
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        if isinstance(exc, TelegramBadRequest) and not any(
            s in exc.message.lower() for s in ("chat not found", "user not found")
        ):
            raise
        event.sent, event.discarded_at, event.last_error = True, now(), type(exc).__name__
        event.attempts += 1
        if row is not None and not event.payload.get("admin"):
            await Staffing(uow.store, await uow.store.get(User, row.user_id)).admin_notice(
                row, "delivery_failed"
            )
    return event.delivered_at is not None
