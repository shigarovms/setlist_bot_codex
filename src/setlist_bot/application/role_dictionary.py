"""Editable per-deployment vocabulary. Historical aliases keep stored assignments readable."""

from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import ProfessionalRole
from setlist_bot.domain.rules import normalize, require
from setlist_bot.domain.staff_roles import AMBIGUOUS, ROLES, RoleIndex


class RoleDictionary:
    def __init__(self, store: Store):
        self.store = store

    async def index(self) -> RoleIndex:
        rows = await self.store.find(ProfessionalRole)
        return RoleIndex([(r.key, r.title, [*r.aliases, *r.retired_aliases]) for r in rows] or None)

    async def entries(self) -> list[ProfessionalRole]:
        # RU: Сериализуем начальное заполнение и правки всего словаря, не чатов.
        # EN: A dedicated transaction lock protects alias uniqueness across concurrent forms.
        await self.store.lock_role_dictionary()
        rows = await self.store.find(ProfessionalRole)
        if not rows:
            rows = [
                ProfessionalRole(key=key, title=title, aliases=aliases.split("|"))
                for key, (title, aliases) in ROLES.items()
            ]
            for row in rows:
                self.store.add(row)
            await self.store.flush()
        return sorted(rows, key=lambda r: normalize(r.title))

    async def save(
        self, identifier: UUID, expected: int, title: str, aliases: list[str]
    ) -> ProfessionalRole:
        rows = await self.entries()
        row = next((r for r in rows if r.id == identifier), None)
        require(type(expected) is int and (row.version if row else 0) == expected, "conflict")
        require(
            isinstance(title, str)
            and 0 < len(title.strip()) <= 100
            and not any(ord(c) < 32 for c in title),
            "role_dictionary_input",
        )
        require(
            isinstance(aliases, list)
            and len(aliases) <= 150
            and all(
                isinstance(a, str) and 0 < len(a.strip()) <= 100 and not any(ord(c) < 32 for c in a)
                for a in aliases
            ),
            "role_dictionary_input",
        )
        title, aliases = title.strip(), [a.strip() for a in aliases]
        key = row.key if row else "role_" + identifier.hex
        archived = list(row.retired_aliases) if row else []
        # RU: Старые названия могли сохраниться в профилях и составах: не меняем их смысл.
        # EN: Removing an alias from the visible list must not reinterpret historical records.
        if row:
            archived = sorted(set([*archived, row.title, *row.aliases]))
        tokens = {normalize(s) for s in [key, title, *aliases, *archived]}
        require(not (tokens & AMBIGUOUS), "role_dictionary_input")
        for other in rows:
            if other.id != identifier:
                require(
                    not tokens.intersection(
                        normalize(s)
                        for s in [other.key, other.title, *other.aliases, *other.retired_aliases]
                    ),
                    "role_dictionary_collision",
                )
        if row is None:
            row = ProfessionalRole(id=identifier, key=key, title=title)
            self.store.add(row)
        else:
            row.version += 1
        row.title, row.aliases, row.retired_aliases = title, aliases, archived
        await self.store.flush()
        return row
