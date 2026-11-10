"""Telegram delivery of the public roster; private profile fields never leave the service."""

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from setlist_bot.domain.entities import (
    ChatBinding,
    Group,
    MemberBoard,
    Membership,
    Outbox,
    StaffContact,
    StaffRole,
    User,
    now,
)
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Response
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.telegram import send_response


async def deliver_members(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> bool:
    assert event.group_id is not None
    group = await uow.store.get(Group, event.group_id)
    boards = await uow.store.find(MemberBoard, group_id=group.id)
    if not boards or group.deleted_at is not None:
        event.sent, event.discarded_at = True, now()
        return False
    board = boards[0]
    if (
        board.group_epoch != group.lifecycle_version
        or board.chat_id != event.chat_id
        or not await uow.store.find(ChatBinding, group_id=group.id, chat_id=board.chat_id)
    ):
        event.sent, event.discarded_at = True, now()
        return False
    members = await uow.store.find(Membership, group_id=group.id, status="active")
    users = {u.id: u for u in await uow.store.get_many(User, {m.user_id for m in members})}
    roles = {r.id: r.name for r in await uow.store.find(StaffRole, group_id=group.id)}
    contacts = await uow.store.find(StaffContact, group_id=group.id, permanent=True)
    by_person: dict[object, list[str]] = {}
    for contact in contacts:
        by_person.setdefault(contact.user_id, []).append(roles[contact.role_id])
    lines = [tr(group.language, "join_board", name=group.name), ""]
    for i, member in enumerate(
        sorted(members, key=lambda m: (m.user_id != group.owner_id, m.id)), 1
    ):
        user = users[member.user_id]
        name = (
            user.profile.get("name")
            or member.musical_role
            or user.display_name
            or str(user.telegram_id)
        )
        names = list(dict.fromkeys(by_person.get(member.user_id, [])))
        lines.append(f"{i}. {name} — " + (", ".join(names) or tr(group.language, "join_no_roles")))
    response = Response(
        "\n".join(lines),
        [(tr(group.language, "join_button", name=group.name), f"join_band {group.id.hex}")],
    )
    response.edit_message_ids = board.message_ids
    try:
        messages = await send_response(bot, board.chat_id, response)
    except TelegramForbiddenError:
        event.sent, event.discarded_at = True, now()
        return False
    # RU: Запоминаем и страницы длинного списка. EN: Reuse every chunk, including long rosters.
    board.message_ids = [m.message_id for m in messages]
    event.sent, event.delivered_at = True, now()
    event.attempts += 1
    event.last_error, event.next_attempt_at = "", None
    return True
