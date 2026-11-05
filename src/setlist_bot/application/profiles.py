"""User-owned professional profile, independent of band membership and bot permissions."""

from typing import Any

from setlist_bot.application.activity import record_activity
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import User
from setlist_bot.domain.rules import require
from setlist_bot.domain.staff_roles import parse_skills


class Profiles:
    def __init__(self, store: Store, user: User):
        self.store, self.user = store, user

    async def save(self, expected: int, values: dict[str, Any]) -> User:
        user = await self.store.get(User, self.user.id, lock=True)
        require(type(expected) is int and user.profile_version == expected, "conflict")
        require(
            set(values) <= {"name", "roles", "city", "substitutions", "travel", "about"}, "input"
        )
        for key, limit in (("name", 200), ("city", 200), ("about", 1000)):
            require(
                isinstance(values.get(key, ""), str) and len(values.get(key, "")) <= limit, "input"
            )
        require(
            all(type(values[k]) is bool for k in ("substitutions", "travel") if k in values),
            "input",
        )
        roles = values.get("roles", [])
        require(isinstance(roles, list) and all(isinstance(r, str) for r in roles), "input")
        if roles:
            roles = parse_skills("\n".join(roles))
        user.profile = {**values, "roles": roles}
        if values.get("name"):
            user.display_name = values["name"]
        user.profile_version += 1
        record_activity(self.store, self.user, "profile_saved", user.id, fields=sorted(values))
        return user
