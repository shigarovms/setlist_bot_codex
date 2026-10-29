"""Durable UI deliveries for internet search and owner permission requests."""

import json
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from setlist_bot.application.music_search import MusicSearches
from setlist_bot.application.setlist_menus import SetlistMenus
from setlist_bot.domain.entities import (
    ChatBinding,
    Confirmation,
    Group,
    MusicSearch,
    Outbox,
    User,
    now,
)
from setlist_bot.domain.rules import AppError, require
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.music_search import music_view, remember_music
from setlist_bot.transport.telegram import send_response


async def result_notice(
    uow: PostgresUnitOfWork, job: MusicSearch, username: str, *, delivered: bool
) -> None:
    origin = job.data.get("origin_chat_id")
    if not origin or not await uow.store.find(ChatBinding, group_id=job.group_id, chat_id=origin):
        return
    marker = "failed" if job.status == "failed" else "delivered" if delivered else "unavailable"
    if job.data.get("notice") == marker:
        return
    owner = await uow.store.get(User, job.owner_id)
    group = await uow.store.get(Group, job.group_id)
    c = Controller(uow.store, owner, origin, private=False)
    buttons: list[list[str]]
    if job.status == "failed":
        notice_text = tr(group.language, "music_failed")
        buttons = []
    elif delivered:
        notice_text = tr(group.language, "music_delivered", name=await c.groups.name(group.id))
        buttons = []
    else:
        notice_text = tr(group.language, "music_open_private")
        buttons = [
            [
                tr(group.language, "music_open_results"),
                f"https://t.me/{username}?start=musicjob_{job.id.hex}",
            ]
        ]
    uow.store.add(
        Outbox(
            group_id=group.id,
            chat_id=origin,
            support_kind="music_note",
            text=notice_text,
            buttons=buttons,
        )
    )
    job.data = {**job.data, "notice": marker}


async def deliver_music(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> bool:
    try:
        job = await uow.store.get(MusicSearch, UUID(event.payload["job_id"]))
        owner = await uow.store.get(User, job.owner_id)
        job = await MusicSearches(uow.store, owner, owner.telegram_id).load(job.id)
        if job.status != event.payload["status"]:
            event.sent = True
            return False
        private = event.chat_id > 0
        if not private:
            require(
                job.data.get("origin_chat_id") == event.chat_id
                and bool(
                    await uow.store.find(ChatBinding, group_id=job.group_id, chat_id=event.chat_id)
                ),
                "input_expired",
            )
        me = await bot.me() if job.data.get("origin_chat_id") else None
        controller = Controller(
            uow.store,
            owner,
            event.chat_id,
            private=private,
            bot_username=(me.username or "") if me else "",
        )
        if not private:
            controller.language = (await uow.store.get(Group, job.group_id)).language
        response = await music_view(controller, job)
        messages = await send_response(bot, event.chat_id, response)
        await remember_music(uow.store, response, [message.message_id for message in messages])
        event.sent, event.delivered_at = True, now()
        event.attempts += 1
        if private and job.status in ("choosing", "failed"):
            await result_notice(uow, job, (me.username or "") if me else "", delivered=True)
    except (AppError, TelegramForbiddenError, TelegramBadRequest) as exc:
        # Bad content errors must remain visible/retryable; only known private-chat
        # access failures get the fallback. Timeouts and 429 are handled by the outbox.
        if isinstance(exc, TelegramBadRequest) and not any(
            x in exc.message.lower() for x in ("chat not found", "user not found")
        ):
            raise
        event.sent, event.discarded_at = True, now()
        event.last_error = type(exc).__name__
        if (
            isinstance(exc, (TelegramForbiddenError, TelegramBadRequest))
            and event.chat_id > 0
            and job.status in ("choosing", "failed")
        ):
            me = await bot.me()
            await result_notice(uow, job, me.username or "", delivered=False)
    return event.delivered_at is not None


async def deliver_collective(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> bool:
    requests = await uow.store.find(Confirmation, id=UUID(event.payload["request_id"]))
    if not requests:
        event.sent, event.discarded_at = True, now()
        return False
    row = requests[0]
    data = SetlistMenus.data(row)
    group = await uow.store.get(Group, UUID(data["group_id"]))
    owner = await uow.store.get(User, group.owner_id) if group.owner_id else None
    requester = await uow.store.get(User, row.user_id)
    if (
        data.get("status") != "pending"
        or row.expires_at <= now()
        or group.deleted_at
        or str(group.owner_id) != data["owner_id"]
        or group.lifecycle_version != data["group_epoch"]
        or owner is None
        or owner.telegram_id != event.chat_id
    ):
        event.sent = True
        return False
    try:
        from setlist_bot.transport.controller import Response

        messages = await send_response(
            bot, event.chat_id, Response(event.text, [(a, b) for a, b in event.buttons])
        )
        row.command = json.dumps({**data, "owner_message_ids": [m.message_id for m in messages]})
        event.sent, event.delivered_at = True, now()
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        if isinstance(exc, TelegramBadRequest) and not any(
            x in exc.message.lower() for x in ("chat not found", "user not found")
        ):
            raise
        SetlistMenus.write(row, {**data, "status": "undeliverable"})
        event.sent, event.discarded_at, event.last_error = True, now(), type(exc).__name__
        # The owner has not started the bot: notify the requester without exposing
        # private owner details. A new request is allowed after this terminal state.
        uow.store.add(
            Outbox(
                chat_id=row.chat_id,
                support_kind="role_done",
                text=tr(requester.language, "admin_unavailable"),
            )
        )
    event.attempts += 1
    return event.delivered_at is not None
