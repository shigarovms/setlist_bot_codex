"""RU: Управление личными ссылками. EN: No Google/Apple credentials or raw tokens in DB."""

import hashlib
import secrets
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import CalendarSubscription, Group, Membership, User
from setlist_bot.domain.rules import require


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class Calendars:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def groups(self) -> list[Group]:
        memberships = await self.store.find(Membership, user_id=self.user.id, status="active")
        groups = await self.store.get_many(Group, {member.group_id for member in memberships})
        return sorted(
            (group for group in groups if group.deleted_at is None), key=lambda g: (g.name, g.id)
        )

    async def subscription(self, group_id: UUID | None) -> CalendarSubscription | None:
        rows = await self.store.find(CalendarSubscription, owner_id=self.user.id, group_id=group_id)
        return rows[0] if rows else None

    async def create(self, group_id: UUID | None) -> tuple[CalendarSubscription, str | None]:
        # RU: Один порядок блокировок, в том числе для прямых вызовов без Processor.
        # EN: Serialize double-clicks before checking a nullable unique scope.
        if group_id is not None:
            await Groups(self.store, self.user).access(group_id)
        await self.store.get(User, self.user.id, lock=True)
        row = await self.subscription(group_id)
        if row is not None and row.enabled:
            return row, None
        token = secrets.token_urlsafe(32)
        if row is None:
            row = CalendarSubscription(
                owner_id=self.user.id,
                group_id=group_id,
                token_hash=token_hash(token),
                language=self.user.language,
            )
            self.store.add(row)
        else:
            row.enabled, row.token_hash = True, token_hash(token)
            row.language, row.version = self.user.language, row.version + 1
        record_activity(self.store, self.user, "calendar_created", row.id)
        return row, token

    async def change(
        self, identifier: UUID, version: int, action: str
    ) -> tuple[CalendarSubscription, str | None]:
        row = await self.store.get(CalendarSubscription, identifier)
        require(row.owner_id == self.user.id, "forbidden")
        if action == "rotate" and row.group_id is not None:
            await Groups(self.store, self.user).access(row.group_id)
        row = await self.store.get(CalendarSubscription, identifier, lock=True)
        require(row.version == version and row.enabled, "input_expired")
        require(action in ("rotate", "disable"), "input")
        token = secrets.token_urlsafe(32) if action == "rotate" else None
        row.token_hash = token_hash(token or secrets.token_urlsafe(32))
        row.enabled, row.version = action == "rotate", row.version + 1
        record_activity(self.store, self.user, "calendar_" + action, row.id)
        return row, token
