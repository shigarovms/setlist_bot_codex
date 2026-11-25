"""Shared event timetable, access checks and reminder scheduling."""

import re
from datetime import datetime, time, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from setlist_bot.application.ports import Store
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.concerts import concert_label
from setlist_bot.domain.entities import Concert, Group, Membership, Outbox, User
from setlist_bot.domain.rules import AppError, require

DEFAULT_ZONE = "Europe/Moscow"
TIMING_KIND = "timing"
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
ZONES = sorted(available_timezones() - {"localtime", "posixrules"})


def zone(event: Concert) -> str:
    return cast(str, event.timing.get("zone", DEFAULT_ZONE))


def version(event: Concert) -> int:
    return cast(int, event.timing.get("version", 0))


def timing_rows(value: Any) -> list[dict[str, Any]]:
    require(isinstance(value, list) and len(value) <= 60, "timing_input")
    rows = []
    for row in value:
        require(isinstance(row, dict), "timing_input")
        start, end, label = row.get("start"), row.get("end", ""), row.get("label")
        next_day = row.get("next_day", False)
        require(
            isinstance(start, str)
            and CLOCK.fullmatch(start) is not None
            and isinstance(end, str)
            and (not end or CLOCK.fullmatch(end) is not None and end != start)
            and isinstance(label, str)
            and 0 < len(label.strip()) <= 200
            and all(ord(char) >= 32 for char in label)
            and type(next_day) is bool,
            "timing_input",
        )
        rows.append({"start": start, "end": end, "label": label.strip(), "next_day": next_day})
    return sorted(rows, key=lambda row: (row["next_day"], row["start"]))


async def admins(store: Store, group_id: UUID) -> list[Membership]:
    return await store.find(Membership, group_id=group_id, status="active", role="admin")


async def participants(store: Store, group: Group, event: Concert) -> set[UUID]:
    return {
        row.user_id
        for row in await store.roster_invitations(group.id, event.id)
        if row.status == "confirmed" and Staffing.valid(row, event, group)
    }


async def responsible(store: Store, group: Group, event: Concert) -> UUID | None:
    identifier = event.timing.get("responsible")
    if identifier and await store.find(
        Membership, group_id=group.id, user_id=UUID(identifier), status="active"
    ):
        return UUID(identifier)
    administrators = await admins(store, group.id)
    eligible = {row.user_id for row in administrators}
    return group.owner_id if group.owner_id in eligible else min(eligible, default=None)


class WorkspaceTiming:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def scope(self, event_id: UUID, group_id: UUID) -> tuple[Group, Concert, bool, bool]:
        group = await self.store.get(Group, group_id, lock=True)
        event = await self.store.get(Concert, event_id)
        require(event.group_id == group.id, "not_found")
        require(not group.deleted_at and not event.deleted_at, "in_trash")
        memberships = await self.store.find(
            Membership, group_id=group.id, user_id=self.user.id, status="active"
        )
        admin = bool(memberships and memberships[0].role == "admin")
        editor = admin or bool(memberships and event.timing.get("responsible") == str(self.user.id))
        if not editor:
            require(self.user.id in await participants(self.store, group, event), "forbidden")
        return group, event, admin, editor

    async def view(self, event_id: UUID, group_id: UUID) -> dict[str, Any]:
        group, event, admin, editor = await self.scope(event_id, group_id)
        return {
            "language": self.user.language,
            "band": group.name,
            "event": concert_label(event, self.user.language),
            "rows": event.timing.get("rows", []),
            "version": version(event),
            "epoch": event.lifecycle_version,
            "zone": zone(event),
            "zones": ZONES if editor else [],
            "preliminary": event.timing.get("preliminary", False),
            "responsible": event.timing.get("responsible", "") if admin else "",
            "members": await self.members(group) if admin else [],
            "admin": admin,
            "editor": editor,
        }

    async def members(self, group: Group) -> list[dict[str, str]]:
        people = await self.store.get_many(
            User,
            {
                row.user_id
                for row in await self.store.find(Membership, group_id=group.id, status="active")
            },
        )
        return [
            {
                "id": str(person.id),
                "name": person.profile.get("name")
                or person.display_name
                or (f"@{person.username}" if person.username else ""),
            }
            for person in sorted(
                people, key=lambda person: (person.display_name.casefold(), person.id)
            )
        ]

    async def command(self, group_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
        event_id = UUID(body["performance"])
        _, event, admin, editor = await self.scope(event_id, group_id)
        require(editor, "admin_only")
        require(
            type(body.get("version")) is int
            and body["version"] == version(event)
            and body.get("epoch") == event.lifecycle_version,
            "conflict",
        )
        action = body.get("action")
        require(action in ("save", "notify"), "timing_input")
        command = {
            "action": action,
            "performance": str(event_id),
            "version": version(event),
            "epoch": event.lifecycle_version,
        }
        if action == "notify":
            return command
        name = body.get("zone", zone(event))
        try:
            require(isinstance(name, str) and name in ZONES, "timing_input")
            ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AppError("timing_input") from exc
        preliminary = body.get("preliminary")
        require(type(preliminary) is bool, "timing_input")
        command.update(rows=timing_rows(body.get("rows")), zone=name, preliminary=preliminary)
        if "responsible" in body:
            require(admin, "admin_only")
            identifier = body["responsible"]
            if identifier:
                person = UUID(identifier)
                require(
                    bool(
                        await self.store.find(
                            Membership, group_id=group_id, user_id=person, status="active"
                        )
                    ),
                    "timing_input",
                )
                identifier = str(person)
            command["responsible"] = identifier or ""
        return command

    async def apply(self, group_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
        command = await self.command(group_id, body)
        group = await self.store.get(Group, group_id)
        event = await self.store.get(Concert, UUID(command["performance"]))
        if command["action"] == "notify":
            require(bool(event.timing.get("rows")), "timing_empty")
            queued = await queue_timing(self.store, group, event)
            return {"queued": queued}
        updates = {key: command[key] for key in ("rows", "zone", "preliminary")}
        if "responsible" in command:
            updates["responsible"] = command["responsible"]
        if any(event.timing.get(key) != value for key, value in updates.items()):
            event.timing = {**event.timing, **updates, "version": version(event) + 1}
        return {"version": version(event)}


async def queue_timing(store: Store, group: Group, event: Concert, *, kind: str = "manual") -> int:
    recipients: set[UUID]
    if kind == "warning":
        owner = await responsible(store, group, event)
        recipients = {owner} if owner else set()
    else:
        recipients = await participants(store, group, event)
    people = await store.get_many(User, recipients)
    existing = (
        await store.find(Outbox, concert_id=event.id, support_kind=TIMING_KIND)
        if kind == "automatic"
        else []
    )
    queued = 0
    for person in people:
        if any(
            row.chat_id == person.telegram_id
            and row.payload.get("kind") == "manual"
            and row.payload.get("rows") == event.timing.get("rows")
            and row.payload.get("preliminary") == event.timing.get("preliminary", False)
            and row.payload.get("zone") == zone(event)
            and row.payload.get("date") == event.date.isoformat()
            and row.payload.get("epoch") == event.lifecycle_version
            and (not row.sent or row.delivered_at is not None)
            for row in existing
        ):
            continue
        store.add(
            Outbox(
                chat_id=person.telegram_id,
                group_id=group.id,
                concert_id=event.id,
                support_kind=TIMING_KIND,
                text="",
                payload={
                    "user": str(person.id),
                    "kind": kind,
                    "version": version(event),
                    "date": event.date.isoformat(),
                    "epoch": event.lifecycle_version,
                    "rows": event.timing.get("rows", []),
                    "zone": zone(event),
                    "preliminary": event.timing.get("preliminary", False),
                },
            )
        )
        queued += 1
    return queued


async def schedule_timing(store: Store, group_id: UUID, event_id: UUID, instant: datetime) -> None:
    group = await store.get(Group, group_id, lock=True)
    events = await store.find(Concert, id=event_id, group_id=group_id, deleted_at=None)
    if not events:
        return
    event = events[0]
    if group.deleted_at or event.deleted_at:
        return
    local = instant.astimezone(ZoneInfo(zone(event)))
    if event.date != local.date() + timedelta(days=1) or local.time() < time(18):
        return
    kind = "warning" if local.time() < time(19) else "automatic"
    stamp = [event.date.isoformat(), event.lifecycle_version]
    if event.timing.get(kind) == stamp:
        return
    if kind == "warning" or event.timing.get("rows"):
        await queue_timing(store, group, event, kind=kind)
    event.timing = {**event.timing, kind: stamp}
