"""Band directory and saved lineups for the Mini App and browser."""

from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.user_lookup import checked_target, known_user
from setlist_bot.application.workspace_team import person_name, text_field
from setlist_bot.domain.entities import LineupTemplate, Membership, StaffContact, StaffRole, User
from setlist_bot.domain.rules import normalize, require
from setlist_bot.domain.staff_input import role_person
from setlist_bot.domain.telegram_identity import fallback_profile, person_caption

PREFERENCES = ("preferred", "normal", "hidden")


def role_name(body: dict[str, Any]) -> str:
    role = text_field(body, "role", 100)
    require(not any(ord(char) < 32 for char in role), "input")
    return role


def lineup_input(body: dict[str, Any], *, preview: bool) -> list[dict[str, str]]:
    if preview and "quick" in body:
        lines = text_field(body, "quick", 8000).splitlines()
        sides = [role_person(line) for line in lines if line.strip()]
        rows: Any = [{"role": role, "reference": person} for role, person in sides]
    else:
        rows = body.get("rows")
    require(isinstance(rows, list) and len(rows) <= 40, "input")
    result = []
    for row in rows:
        require(isinstance(row, dict), "input")
        role = role_name(row)
        raw = row.get("reference", "")
        require(isinstance(raw, str) and len(raw.strip()) <= 64, "input")
        reference = raw.strip()
        parsed = {"role": role, "reference": reference}
        if reference and not preview:
            parsed["person"] = str(UUID(text_field(row, "person", 36)))
        result.append(parsed)
    return result


class WorkspaceStaffing:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)
        self.staffing = Staffing(store, user)

    async def directory(
        self, group_id: UUID, section: str, record: UUID | None, query: str, page: int, size: int
    ) -> dict[str, Any]:
        group = await self.groups.access(group_id, admin=True)
        context = {"language": self.user.language, "band": group.name}
        if section == "contacts":
            return {**context, **await self.contacts(group_id, record, query, page, size)}
        if record:
            row = await self.template(group_id, record)
            return {**context, "lineup": await self.lineup(row)}
        rows = [
            row
            for row in await self.store.find(LineupTemplate, group_id=group_id)
            if normalize(query) in normalize(row.name)
        ]
        rows.sort(key=lambda row: (row.name.casefold(), row.id))
        return {
            **context,
            "has_next": len(rows) > (page + 1) * size,
            "rows": [
                {"id": str(row.id), "name": row.name, "count": len(row.slots)}
                for row in rows[page * size : (page + 1) * size]
            ],
        }

    async def contacts(
        self, group_id: UUID, record: UUID | None, query: str, page: int, size: int
    ) -> dict[str, Any]:
        rows = (
            [await self.store.get(StaffContact, record)]
            if record
            else await self.store.find(StaffContact, group_id=group_id, permanent=False)
        )
        require(all(row.group_id == group_id and not row.permanent for row in rows), "not_found")
        roles = {
            r.id: r.name
            for r in await self.store.get_many(StaffRole, {row.role_id for row in rows})
        }
        people = {u.id: u for u in await self.store.get_many(User, {row.user_id for row in rows})}
        data: list[dict[str, Any]] = []
        for row in rows:
            person = people[row.user_id]
            name, role = person_name(person), roles[row.role_id]
            searchable = f"{name} {role} @{person.username} {person.telegram_id}"
            if normalize(query) not in normalize(searchable):
                continue
            item = {
                "id": str(row.id),
                "name": name,
                "role": role,
                "preference": row.preference,
                "experience": row.worked_count,
                "version": row.version,
            }
            if record:
                item["profile"] = {
                    key: person.profile[key]
                    for key in ("roles", "city", "substitutions", "travel", "about")
                    if key in person.profile
                }
            data.append(item)
        if record:
            require(bool(data), "not_found")
            return {"contact": data[0]}
        data.sort(key=lambda row: (row["role"].casefold(), row["name"].casefold(), row["id"]))
        return {
            "rows": data[page * size : (page + 1) * size],
            "has_next": len(data) > (page + 1) * size,
        }

    async def template(self, group_id: UUID, record: UUID) -> LineupTemplate:
        row = await self.store.get(LineupTemplate, record)
        require(row.group_id == group_id, "not_found")
        return row

    async def lineup(self, row: LineupTemplate) -> dict[str, Any]:
        defaults = await self.staffing.template_people(row)
        saved = [UUID(item["user_id"]) if item.get("user_id") else None for item in row.slots]
        people = {
            person.id: person
            for person in await self.store.get_many(
                User, {p for p in [*defaults, *saved] if p is not None}
            )
        }
        names = {
            str(role.id): role.name
            for role in await self.store.get_many(
                StaffRole, {UUID(slot["role_id"]) for slot in row.slots}
            )
        }
        slots = []
        for slot, fallback, default in zip(row.slots, saved, defaults, strict=True):
            person = people.get(fallback) if fallback else None
            chosen = people.get(default) if default else None
            slots.append(
                {
                    "role": names[slot["role_id"]],
                    "reference": str(person.telegram_id) if person else "",
                    "name": person_caption(person) if person else "",
                    "username": person.username if person else "",
                    "profile_url": fallback_profile(person) if person else None,
                    "suggested": person_caption(chosen) if chosen else "",
                    "suggested_profile_url": fallback_profile(chosen) if chosen else None,
                }
            )
        return {"id": str(row.id), "version": row.version, "name": row.name, "rows": slots}

    async def preview(self, group_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
        await self.groups.access(group_id, admin=True)
        name = text_field(body, "name", 100)
        registered = {
            row.user_id
            for row in await self.store.find(Membership, group_id=group_id, status="active")
        }
        rows: list[dict[str, Any]] = []
        for row in lineup_input(body, preview=True):
            entry: dict[str, Any] = dict(row)
            if row["reference"]:
                person = await known_user(self.store, row["reference"])
                entry.update(
                    person=str(person.id),
                    name=person_caption(person, registered=person.id in registered),
                    username=person.username,
                    profile_url=fallback_profile(person),
                )
            rows.append(entry)
        return {"name": name, "rows": rows}

    @staticmethod
    def command(body: dict[str, Any]) -> dict[str, Any]:
        action = text_field(body, "action", 30)
        require(
            action
            in (
                "contact_add",
                "contact_preference",
                "lineup_create",
                "lineup_update",
                "lineup_copy",
                "lineup_delete",
            ),
            "input",
        )
        command: dict[str, Any] = {"action": action}
        if action == "contact_add":
            command["role"] = role_name(body)
            command["reference"] = text_field(body, "reference", 64)
            command["person"] = str(UUID(text_field(body, "person", 36)))
        if action in ("lineup_create", "lineup_update", "lineup_copy"):
            command["name"] = text_field(body, "name", 100)
        if action in ("lineup_create", "lineup_update"):
            command["rows"] = lineup_input(body, preview=False)
            require(action != "lineup_create" or bool(command["rows"]), "input")
        if action not in ("contact_add", "lineup_create"):
            command["target"] = str(UUID(text_field(body, "target", 36)))
            require(type(body.get("version")) is int, "input")
            command["version"] = body["version"]
        if action == "contact_preference":
            require(body.get("preference") in PREFERENCES, "input")
            command["preference"] = body["preference"]
        return command

    async def resolve(self, group_id: UUID, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        people = [
            await checked_target(self.store, UUID(row["person"]), row["reference"])
            if row["reference"]
            else None
            for row in rows
        ]
        result = []
        for row, person in zip(rows, people, strict=True):
            role = await self.staffing.role(group_id, row["role"])
            result.append({"role_id": str(role.id), "user_id": str(person.id) if person else None})
        return result

    async def apply(self, group_id: UUID, command: dict[str, Any]) -> dict[str, Any]:
        await self.groups.access(group_id, admin=True)
        action = command["action"]
        if action == "contact_add":
            person = await checked_target(self.store, UUID(command["person"]), command["reference"])
            role = await self.staffing.role(group_id, command["role"])
            contact = await self.staffing.contact(group_id, role.id, person.id)
            return {"contact": str(contact.id), "permanent": contact.permanent}
        if action == "contact_preference":
            contact = await self.store.get(StaffContact, UUID(command["target"]))
            require(
                contact.group_id == group_id
                and not contact.permanent
                and contact.version == command["version"],
                "input_expired",
            )
            await self.staffing.contact(
                group_id, contact.role_id, contact.user_id, command["preference"]
            )
            return {"contact": str(contact.id)}
        row = None
        if action != "lineup_create":
            row = await self.template(group_id, UUID(command["target"]))
            require(row.version == command["version"], "input_expired")
        if action == "lineup_delete":
            assert row is not None
            await self.staffing.edit_template(row.id, row.version, delete=True)
            return {}
        name = command["name"]
        if action == "lineup_update":
            assert row is not None
            values = await self.resolve(group_id, command["rows"])
            await self.staffing.edit_template(
                row.id,
                row.version,
                name=name if name != row.name else None,
                values=values if values != row.slots else None,
            )
        else:
            require(
                not await self.store.find(
                    LineupTemplate, group_id=group_id, name_key=normalize(name)
                ),
                "input",
            )
            values = (
                [dict(slot) for slot in row.slots]
                if row
                else await self.resolve(group_id, command["rows"])
            )
            row = await self.staffing.template(group_id, name, values)
        return {"lineup": str(row.id)}
