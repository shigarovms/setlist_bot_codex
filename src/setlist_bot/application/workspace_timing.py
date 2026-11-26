"""Shared event timetable, access checks and reminder scheduling."""

import re
from datetime import datetime, time, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from setlist_bot.application.ports import Store
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.cities import city_timezone
from setlist_bot.domain.concerts import concert_label
from setlist_bot.domain.entities import Concert, Group, Membership, Outbox, User
from setlist_bot.domain.rules import AppError, require

DEFAULT_ZONE = "Europe/Moscow"
TIMING_KIND = "timing"
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
ZONES = sorted(available_timezones() - {"localtime", "posixrules"})
ZONE_CITIES = {
    "Europe/Kaliningrad": ("Калининград", "Kaliningrad"),
    "Europe/Moscow": ("Москва", "Moscow"),
    "Europe/Samara": ("Самара", "Samara"),
    "Asia/Yekaterinburg": ("Екатеринбург", "Yekaterinburg"),
    "Asia/Omsk": ("Омск", "Omsk"),
    "Asia/Novosibirsk": ("Новосибирск", "Novosibirsk"),
    "Asia/Irkutsk": ("Иркутск", "Irkutsk"),
    "Asia/Yakutsk": ("Якутск", "Yakutsk"),
    "Asia/Vladivostok": ("Владивосток", "Vladivostok"),
    "Asia/Magadan": ("Магадан", "Magadan"),
    "Asia/Kamchatka": ("Петропавловск-Камчатский", "Petropavlovsk-Kamchatsky"),
}


def automatic_zone(event: Concert) -> str:
    return city_timezone(event.city) or DEFAULT_ZONE


def zone(event: Concert) -> str:
    return cast(str, event.timing.get("zone")) or automatic_zone(event)


def zone_label(identifier: str, language: str, city: str = "") -> str:
    labels = ZONE_CITIES.get(identifier)
    return labels[language == "en"] if labels else city or identifier


def version(event: Concert) -> int:
    return cast(int, event.timing.get("version", 0))


def timing_rows(value: Any) -> list[dict[str, Any]]:
    require(isinstance(value, list) and len(value) <= 60, "timing_input")
    rows = []
    previous, day = "", 0
    for row in value:
        require(isinstance(row, dict), "timing_input")
        start, end, label = row.get("start"), row.get("end", ""), row.get("label")
        require(
            isinstance(start, str)
            and CLOCK.fullmatch(start) is not None
            and isinstance(end, str)
            and (not end or CLOCK.fullmatch(end) is not None and end != start)
            and isinstance(label, str)
            and 0 < len(label.strip()) <= 200
            and all(ord(char) >= 32 for char in label),
            "timing_input",
        )
        day += start < previous
        rows.append({"start": start, "end": end, "label": label.strip(), "day": day})
        previous = start
    return rows


def timing_day(row: dict[str, Any]) -> int:
    return int(row.get("day", row.get("next_day", False)))


async def admins(store: Store, group_id: UUID) -> list[Membership]:
    return await store.find(Membership, group_id=group_id, status="active", role="admin")


async def participants(store: Store, group: Group, event: Concert) -> set[UUID]:
    return {
        row.user_id
        for row in await store.roster_invitations(group.id, event.id)
        if row.status == "confirmed" and Staffing.valid(row, event, group)
    }


async def responsible(store: Store, group: Group) -> UUID | None:
    identifier = group.timing_coordinator_id
    if identifier and await store.find(
        Membership, group_id=group.id, user_id=identifier, status="active"
    ):
        return identifier
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
        editor = admin or bool(memberships and group.timing_coordinator_id == self.user.id)
        if not editor:
            require(self.user.id in await participants(self.store, group, event), "forbidden")
        return group, event, admin, editor

    async def view(self, event_id: UUID, group_id: UUID) -> dict[str, Any]:
        group, event, admin, editor = await self.scope(event_id, group_id)
        auto = automatic_zone(event)
        manual = event.timing.get("zone", "")
        effective = zone(event)
        choices = dict.fromkeys([*ZONE_CITIES, auto, effective])
        return {
            "language": self.user.language,
            "band": group.name,
            "event": concert_label(event, self.user.language),
            "date": event.date.isoformat(),
            "rows": event.timing.get("rows", []),
            "version": version(event),
            "epoch": event.lifecycle_version,
            "zone": effective,
            "zone_label": zone_label(
                effective, self.user.language, event.city if effective == auto else ""
            ),
            "manual_zone": manual,
            "auto_zone": zone_label(auto, self.user.language, event.city),
            "zones": [
                {
                    "id": identifier,
                    "label": zone_label(
                        identifier, self.user.language, event.city if identifier == auto else ""
                    ),
                }
                for identifier in choices
            ]
            if editor
            else [],
            "preliminary": event.timing.get("preliminary", not event.timing.get("rows")),
            "admin": admin,
            "editor": editor,
        }

    async def command(self, group_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
        event_id = UUID(body["performance"])
        _, event, _, editor = await self.scope(event_id, group_id)
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
        name = body.get("zone", event.timing.get("zone", ""))
        try:
            require(isinstance(name, str) and (not name or name in ZONES), "timing_input")
            if name:
                ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AppError("timing_input") from exc
        preliminary = body.get("preliminary")
        require(type(preliminary) is bool, "timing_input")
        rows = timing_rows(body.get("rows"))
        saved = event.timing.get("rows", [])
        if timing_rows(saved) == rows:
            rows = saved
        command.update(rows=rows, zone=name, preliminary=preliminary)
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
        if any(event.timing.get(key) != value for key, value in updates.items()):
            event.timing = {**event.timing, **updates, "version": version(event) + 1}
        return {"version": version(event)}


async def queue_timing(store: Store, group: Group, event: Concert, *, kind: str = "manual") -> int:
    recipients: set[UUID]
    if kind == "warning":
        owner = await responsible(store, group)
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
