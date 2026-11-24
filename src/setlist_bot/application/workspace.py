"""Navigation for the member-facing workspace, independent of the client."""

from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import Group, Membership, User
from setlist_bot.domain.rules import AppError

PAGE_SIZE = 20


class Workspace:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def bands(self) -> dict[str, Any]:
        memberships = await self.store.find(Membership, user_id=self.user.id, status="active")
        groups = await self.store.get_many(Group, {member.group_id for member in memberships})
        return {
            "language": self.user.language,
            "bands": [
                {"id": str(group.id), "name": group.name}
                for group in sorted(groups, key=lambda row: (row.name.casefold(), row.id))
                if group.deleted_at is None
            ],
        }

    async def performances(self, group_id: UUID, *, past: bool, page: int) -> dict[str, Any]:
        groups = Groups(self.store, self.user)
        group = await groups.access(group_id)
        try:
            await groups.can_suggest(group_id)
            can_edit = True
        except AppError as exc:
            if exc.code != "admin_suggestions_only":
                raise
            can_edit = False
        rows = await self.store.concerts_for_user(
            self.user.id,
            today(),
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE + 1,
            past=past,
            group_id=group_id,
        )
        return {
            "language": self.user.language,
            "band": group.name,
            "can_edit_draft": can_edit,
            "has_next": len(rows) > PAGE_SIZE,
            "performances": [
                {
                    "id": str(row.id),
                    "date": row.date.isoformat(),
                    "title": row.title,
                    "city": row.city,
                }
                for row, _ in rows[:PAGE_SIZE]
            ],
        }
