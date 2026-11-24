"""Scoped replacement records for the workspace views and delivery worker."""

from typing import Any
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Concert, GigInvitation, StaffReplacement, now
from setlist_bot.domain.rules import require


def replacement_data(row: StaffReplacement) -> dict[str, Any]:
    return {"id": str(row.id), "version": row.version}


async def active_replacements(
    store: Store, event: Concert, *, source_id: UUID | None = None
) -> list[StaffReplacement]:
    filters = {"source_id": source_id} if source_id is not None else {}
    rows = await store.find(
        StaffReplacement, group_id=event.group_id, concert_id=event.id, status="pending", **filters
    )
    return [row for row in rows if row.expires_at > now()]


async def replacement_source(
    store: Store, identifier: UUID, group_id: UUID, event_id: UUID
) -> tuple[StaffReplacement, GigInvitation]:
    row = await store.get(StaffReplacement, identifier)
    require(row.group_id == group_id and row.concert_id == event_id, "not_found")
    source = await store.get(GigInvitation, row.source_id)
    require(source.group_id == group_id and source.concert_id == event_id, "not_found")
    return row, source
