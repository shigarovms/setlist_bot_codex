"""Resolve a known Telegram account once, then authorize by immutable user ID."""

from datetime import UTC, datetime
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import User
from setlist_bot.domain.rules import require
from setlist_bot.domain.telegram_identity import normalized_username


async def known_user(store: Store, reference: str) -> User:
    value = reference.strip()
    require(bool(value) and len(value) <= 64, "input")
    if value.isascii() and value.isdigit():
        require(0 < int(value) < 2**63, "input")
        rows = await store.find(User, telegram_id=int(value))
        require(bool(rows), "admin_user_unknown")
        return rows[0]
    username = normalized_username(value)
    rows = await store.find(User, username=username)
    # Telegram handles can be reassigned. Prefer the latest verified observation,
    # never the first matching row; the confirmation always shows a numeric ID.
    dated = [u for u in rows if u.username_seen_at is not None]
    require(bool(dated), "admin_user_unknown")
    dated.sort(key=lambda u: u.username_seen_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    require(
        len(dated) == 1 or dated[0].username_seen_at != dated[1].username_seen_at,
        "admin_user_ambiguous",
    )
    return dated[0]


async def checked_target(store: Store, identifier: UUID, reference: str) -> User:
    # Resolve again inside the mutation transaction to detect reassignment since preview.
    user = await known_user(store, reference)
    require(user.id == identifier, "conflict")
    return user
