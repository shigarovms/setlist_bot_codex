"""No Telegram send: acknowledge an authorized Mini App catalog operation atomically."""

import json
from uuid import UUID

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.miniapp_catalog import MiniCatalog
from setlist_bot.domain.entities import Confirmation, Outbox, User, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def deliver_catalog(uow: PostgresUnitOfWork, event: Outbox) -> bool:
    identifier = UUID(event.payload["request_id"])
    rows = await uow.store.find(Confirmation, id=identifier)
    if not rows:
        event.sent, event.discarded_at = True, now()
        return False
    row = rows[0]
    data = json.loads(row.command)
    try:
        # RU: Ошибка не должна оставить частично записанную песню/алиасы.
        # EN: The savepoint rolls back all catalog writes before recording a terminal error.
        async with uow.session.begin_nested():
            user = await uow.store.get(User, row.user_id)
            session = UUID(data["session"])
            service = MiniDrafts(uow.store, user)
            _, _, concert = await service.load(session)
            require(
                concert.group_id == event.group_id
                and concert.id == event.concert_id
                and user.telegram_id == event.chat_id,
                "miniapp_expired",
            )
            await MiniCatalog(service).execute(session, identifier)
    except AppError as exc:
        row = await uow.store.get(Confirmation, identifier, lock=True)
        row.command = json.dumps({**data, "status": "failed", "result": {"error": exc.code}})
        event.sent, event.discarded_at, event.last_error = True, now(), exc.code
        return False
    event.sent, event.delivered_at = True, now()
    return True
