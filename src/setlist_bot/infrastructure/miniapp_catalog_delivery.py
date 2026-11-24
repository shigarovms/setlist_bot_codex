"""No Telegram send: acknowledge an authorized Mini App catalog operation atomically."""

import json
from uuid import UUID

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.miniapp_catalog import MiniCatalog
from setlist_bot.application.ports import Store
from setlist_bot.application.workspace_gigs import GIG_REPLY
from setlist_bot.application.workspace_roster import ROSTER_KIND, WorkspaceRoster
from setlist_bot.domain.entities import Confirmation, Outbox, User, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def lock_catalog_people(store: Store, event: Outbox) -> AppError | None:
    """Acquire staffing locks before the outbox dispatcher locks the band."""
    rows = await store.find(Confirmation, id=UUID(event.payload["request_id"]))
    if not rows:
        return None
    row = rows[0]
    data = json.loads(row.command)
    if data.get("status") != "pending":
        return None
    if data.get("kind") == GIG_REPLY:
        await store.lock_people({row.user_id})
    if data.get("kind") == ROSTER_KIND and data["roster"]["action"] == "send":
        user = await store.get(User, row.user_id)
        try:
            plan = await WorkspaceRoster(store, user).send_plan(data)
        except AppError as exc:
            return exc
        await store.lock_people({UUID(target["person"]) for target in plan["targets"]})
    return None


async def deliver_catalog(
    uow: PostgresUnitOfWork,
    event: Outbox,
    *,
    people_locked: bool = False,
    preflight: AppError | None = None,
) -> bool:
    identifier = UUID(event.payload["request_id"])
    rows = await uow.store.find(Confirmation, id=identifier)
    if not rows:
        event.sent, event.discarded_at = True, now()
        return False
    row = rows[0]
    data = json.loads(row.command)
    if not people_locked:
        preflight = await lock_catalog_people(uow.store, event)
    try:
        # RU: Ошибка не должна оставить частично записанную песню/алиасы.
        # EN: The savepoint rolls back all catalog writes before recording a terminal error.
        async with uow.session.begin_nested():
            if preflight is not None:
                raise preflight
            user = await uow.store.get(User, row.user_id)
            session = UUID(data["session"])
            service = MiniCatalog(MiniDrafts(uow.store, user))
            target = await service.target(session)
            require(
                target.group_id == event.group_id
                and (target.concert.id if target.concert else None) == event.concert_id
                and user.telegram_id == event.chat_id,
                "miniapp_expired",
            )
            renderer = None
            if data.get("kind") == "miniapp_export" and data["export"]["action"] == "generate":
                from setlist_bot.infrastructure.pdf import PdfDocuments

                renderer = PdfDocuments()
            result = await service.execute(target, identifier, pdf=renderer)
            if notice := result.get("notify"):
                from setlist_bot.transport.shared_requests import queue_admin_request

                await uow.store.flush()
                await queue_admin_request(
                    uow.store, user, await uow.store.get(Confirmation, UUID(notice))
                )
            if resolved := result.get("resolved"):
                from setlist_bot.transport.shared_requests import queue_decision_result

                await queue_decision_result(
                    uow.store, await uow.store.get(Confirmation, UUID(resolved))
                )
    except AppError as exc:
        row = await uow.store.get(Confirmation, identifier, lock=True)
        if data.get("kind") == "miniapp_catalog_import":
            data.pop("source", None)
        row.command = json.dumps({**data, "status": "failed", "result": {"error": exc.code}})
        event.sent, event.discarded_at, event.last_error = True, now(), exc.code
        return False
    event.sent, event.delivered_at = True, now()
    return True
