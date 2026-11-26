"""Create, edit and recover performances through the same services as the bot."""

import json
from collections import Counter
from datetime import date
from typing import Any
from uuid import UUID

from setlist_bot.application.concert_details import PRESET_TYPES, ConcertDetails
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.trash import RETENTION, Trash
from setlist_bot.application.workspace_timing import WorkspaceTiming
from setlist_bot.application.workspace_timing import version as timing_version
from setlist_bot.domain.cities import city_name
from setlist_bot.domain.concerts import concert_label, today
from setlist_bot.domain.entities import (
    Concert,
    Confirmation,
    GigSlot,
    LineupTemplate,
    Setlist,
    Song,
    User,
    now,
)
from setlist_bot.domain.performance_input import (
    PerformanceInput,
    performance_seconds,
    recognize_performance,
)
from setlist_bot.domain.rules import is_pause, normalize, require

FIELDS = {"title": 200, "city": 200, "performance_type": 200, "info": 2000}
DETAIL_FIELDS = {
    "date": "concert_date",
    "duration": "concert_duration",
    "title": "concert_title",
    "city": "concert_city",
    "performance_type": "concert_type",
    "info": "concert_info",
}


def performance_input(concert: Concert) -> PerformanceInput:
    return PerformanceInput(
        concert.date,
        concert.title,
        concert.performance_duration_seconds,
        concert.performance_type,
        concert.city,
        concert.extra_info,
    )


def form_input(body: dict[str, Any]) -> PerformanceInput:
    when = date.fromisoformat(body["date"])
    duration = body["duration"]
    require(isinstance(duration, str) and len(duration) <= 80, "pause_duration")
    seconds = performance_seconds(duration) if duration.strip() else None
    require(not duration.strip() or seconds is not None, "pause_duration")
    fields = {}
    for field, limit in FIELDS.items():
        value = body[field]
        require(isinstance(value, str) and len(value) <= limit, "concert_input")
        value = value.strip()
        require(field == "info" or all(ord(char) >= 32 for char in value), "concert_input")
        fields[field] = "" if value == "-" else value
    return PerformanceInput(when, duration=seconds, **fields)


def duration_text(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes // 60}:{minutes % 60:02d}:{remainder:02d}"


class WorkspacePerformances:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)
        self.trash = Trash(store, user)

    @staticmethod
    def command(body: dict[str, Any]) -> dict[str, Any]:
        action = body.get("action")
        if action == "confirm":
            require(isinstance(body.get("preview"), str), "input")
            return {"action": action, "preview": str(UUID(body["preview"]))}
        require(action in ("create", "update", "delete", "restore"), "input")
        if action == "create" and body.get("quick") is not None:
            quick = body["quick"]
            require(isinstance(quick, str) and 0 < len(quick) <= 4000, "concert_input")
            return {"action": action, "quick": quick, "lineup": body.get("lineup", "auto")}
        command = {"action": action}
        if action != "create":
            require(
                type(body.get("epoch")) is int and isinstance(body.get("performance"), str),
                "input",
            )
            command.update(performance=str(UUID(body["performance"])), epoch=body["epoch"])
        if action in ("update", "delete"):
            require(type(body.get("version")) is int, "input")
            command["version"] = body["version"]
        if action in ("create", "update"):
            command["fields"] = form_input(body).payload()
        if action == "create":
            command["lineup"] = body.get("lineup", "auto")
        if action == "create" and body.get("source"):
            command["source"] = str(UUID(body["source"]))
            for key in ("copy_timing", "copy_lineup", "copy_setlist"):
                require(type(body.get(key)) is bool, "input")
                command[key] = body[key]
        return command

    async def lineups(self, group_id: UUID) -> list[LineupTemplate]:
        return sorted(
            await self.store.find(LineupTemplate, group_id=group_id),
            key=lambda row: (row.created_at, row.id),
        )

    async def options(self, group_id: UUID) -> dict[str, Any]:
        await self.groups.access(group_id, admin=True)
        cutoff = today()
        past = sorted(
            (
                row
                for row in await self.store.find(Concert, group_id=group_id)
                if row.deleted_at is None and row.date < cutoff and row.city.strip()
            ),
            key=lambda row: (row.date, row.id),
            reverse=True,
        )
        counts: Counter[str] = Counter()
        labels: dict[str, str] = {}
        for row in past:
            city = city_name(row.city) or row.city.strip()
            key = normalize(city)
            counts[key] += 1
            labels.setdefault(key, city)
        return {
            "types": PRESET_TYPES,
            "cities": [labels[key] for key, _ in counts.most_common(3)],
            "lineups": [
                {"id": str(row.id), "name": row.name} for row in await self.lineups(group_id)
            ],
        }

    async def history(
        self, group_id: UUID, page: int, *, timing_for: UUID | None = None
    ) -> dict[str, Any]:
        if timing_for is None:
            await self.groups.access(group_id, admin=True)
        else:
            _, _, _, editor = await WorkspaceTiming(self.store, self.user).scope(
                timing_for, group_id
            )
            require(editor, "admin_only")
        all_events = await self.store.find(Concert, group_id=group_id, deleted_at=None)
        past = sorted(
            (
                row
                for row in all_events
                if row.date < today() and (timing_for is None or row.timing.get("rows"))
            ),
            key=lambda row: (row.date, row.id),
            reverse=True,
        )
        counts = Counter(row.template_source_id for row in all_events if row.template_source_id)
        recommended = [row for row in past if counts[row.id]]
        recommended.sort(key=lambda row: counts[row.id], reverse=True)
        start = page * 10

        def item(row: Concert) -> dict[str, Any]:
            result = {
                "id": str(row.id),
                "label": concert_label(row, self.user.language),
                "date": row.date.isoformat(),
                "fields": performance_input(row).payload(),
                "has_timing": bool(row.timing.get("rows")),
            }
            if timing_for is not None:
                result["rows"] = row.timing["rows"]
            return result

        return {
            "recommended": [item(row) for row in recommended[:3]] if page == 0 else [],
            "performances": [item(row) for row in past[start : start + 10]],
            "has_next": len(past) > start + 10,
        }

    async def template(self, group_id: UUID, identifier: UUID) -> Concert:
        source = await self.store.get(Concert, identifier)
        require(
            source.group_id == group_id and not source.deleted_at and source.date < today(),
            "not_found",
        )
        return source

    async def source_stamp(self, source: Concert, command: dict[str, Any]) -> dict[str, Any]:
        stamp = {"epoch": source.lifecycle_version, "metadata": source.metadata_version}
        if command["copy_lineup"]:
            stamp["lineup"] = source.lineup_version
        if command["copy_timing"]:
            stamp["timing"] = timing_version(source)
        if command["copy_setlist"]:
            lists = await self.store.find(Setlist, concert_id=source.id, group_id=source.group_id)
            require(bool(lists), "not_found")
            stamp["setlist"] = lists[0].version
        return stamp

    async def source_slots(self, source: Concert) -> list[GigSlot]:
        return await self.store.find(
            GigSlot, group_id=source.group_id, concert_id=source.id, removed=False, required=True
        )

    async def copy_template(
        self, source: Concert, target: Concert, command: dict[str, Any], *, saved_lineup: bool
    ) -> None:
        target.template_source_id = source.id
        if command["copy_timing"] and source.timing.get("rows"):
            target.timing = {
                "rows": source.timing["rows"],
                "preliminary": True,
                "version": 1,
            }
            if source.city == target.city and "zone" in source.timing:
                target.timing["zone"] = source.timing["zone"]
        if command["copy_lineup"] and not saved_lineup:
            slots = await self.source_slots(source)
            candidates = {
                row.slot_id: row.user_id
                for row in await self.store.roster_invitations(source.group_id, source.id)
                if row.status == "confirmed"
                and row.concert_epoch == source.lifecycle_version
                and row.date == source.date
            }
            values = []
            for slot in sorted(slots, key=lambda row: row.position):
                person = candidates.get(slot.id, slot.preferred_user_id)
                values.append(
                    {"role_id": str(slot.role_id), "user_id": str(person) if person else None}
                )
            if values:
                await Staffing(self.store, self.user).replace_uninvited_lineup(
                    target.id, target.lineup_version, source.lineup_name, values
                )
        if command["copy_setlist"]:
            lists = Setlists(self.store, self.user)
            snapshot, _, _ = await lists.read(source.group_id, source.id)
            if not any(block["items"] for block in snapshot["blocks"]):
                return
            song_ids = {
                UUID(item["song_id"])
                for block in snapshot["blocks"]
                for item in block["items"]
                if not is_pause(item)
            }
            songs = await self.store.get_many(Song, song_ids)
            require(all(not song.archived for song in songs), "catalog_changed")
            target_list = await lists.load(target.group_id, target.id)
            await lists._persist(target_list, snapshot)
            await lists._record(target_list, "copied", snapshot)

    async def trash_page(self, group_id: UUID, page: int, limit: int) -> dict[str, Any]:
        group = await self.groups.access(group_id, admin=True)
        cutoff = now()
        deleted = [
            row
            for row in await self.store.find(Concert, group_id=group_id)
            if row.deleted_at is not None and row.deleted_at + RETENTION > cutoff
        ]
        deleted.sort(key=lambda row: (row.deleted_at or cutoff, row.id), reverse=True)
        start = page * limit
        return {
            "language": self.user.language,
            "band": group.name,
            "has_next": len(deleted) > start + limit,
            "performances": [
                {
                    "id": str(row.id),
                    "title": row.title,
                    "label": concert_label(row, self.user.language),
                    "date": row.date.isoformat(),
                    "epoch": row.lifecycle_version,
                    "until": ((row.deleted_at or cutoff) + RETENTION).date().isoformat(),
                }
                for row in deleted[start : start + limit]
            ],
        }

    async def concert(
        self, group_id: UUID, command: dict[str, Any], *, deleted: bool = False
    ) -> Concert:
        concert = await self.store.get(Concert, UUID(command["performance"]))
        require(concert.group_id == group_id, "not_found")
        require(
            (concert.deleted_at is not None) == deleted
            and concert.lifecycle_version == command["epoch"]
            and ("version" not in command or concert.metadata_version == command["version"]),
            "conflict",
        )
        return concert

    async def preview(
        self, group_id: UUID, command: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        await self.groups.access(group_id, admin=True)
        action = command["action"]
        if action == "create":
            lineups = await self.lineups(group_id)
            preferred = command["lineup"]
            require(
                isinstance(preferred, str)
                and preferred in {"auto", "named", "none", *(str(row.id) for row in lineups)},
                "input_expired",
            )
            named = None
            if "quick" in command:
                parsed, named = recognize_performance(command["quick"], lineups)
                fields = parsed.payload()
            else:
                fields = command["fields"]
            if preferred == "auto":
                lineup = named or (lineups[0] if len(lineups) == 1 else None)
                if lineup is None and len(lineups) > 1:
                    return {
                        "choose_lineup": True,
                        "lineups": [{"id": str(row.id), "name": row.name} for row in lineups],
                    }, {}
            elif preferred == "named":
                lineup = named
            else:
                lineup = next((row for row in lineups if str(row.id) == preferred), None)
            change = {"action": action, "fields": fields}
            if lineup:
                change["lineup"] = {"id": str(lineup.id), "version": lineup.version}
            result: dict[str, Any] = {"fields": fields, "lineup": lineup.name if lineup else ""}
            if "source" in command:
                source = await self.template(group_id, UUID(command["source"]))
                for key in ("source", "copy_timing", "copy_lineup", "copy_setlist"):
                    change[key] = command[key]
                change["stamp"] = await self.source_stamp(source, change)
                result["source"] = concert_label(source, self.user.language)
                copied = []
                if change["copy_timing"] and source.timing.get("rows"):
                    copied.append("copy_timing")
                if change["copy_lineup"] and lineup is None and await self.source_slots(source):
                    copied.append("copy_lineup")
                if change["copy_setlist"]:
                    snapshot, _, _ = await Setlists(self.store, self.user).read(group_id, source.id)
                    if any(block["items"] for block in snapshot["blocks"]):
                        copied.append("copy_setlist")
                result["copied"] = copied
            return result, change
        concert = await self.concert(group_id, command, deleted=action == "restore")
        fields = performance_input(concert).payload()
        result = {"fields": fields}
        change = dict(command)
        if action == "update":
            result = {
                "fields": command["fields"],
                "date_changed": fields["date"] != command["fields"]["date"],
            }
        if action == "delete":
            change["fingerprint"] = await self.trash.concert_fingerprint(group_id, concert.id)
        if action == "restore":
            assert concert.deleted_at is not None
            require(concert.deleted_at + RETENTION > now(), "trash_expired")
        return result, change

    async def confirm(self, group_id: UUID, session: UUID, identifier: UUID) -> dict[str, Any]:
        await self.groups.access(group_id, admin=True)
        preview = await self.store.get(Confirmation, identifier, lock=True)
        receipt = json.loads(preview.command)
        require(
            preview.user_id == self.user.id
            and preview.expires_at > now()
            and receipt.get("kind") == "miniapp_performance"
            and receipt.get("session") == str(session)
            and receipt.get("group_id") == str(group_id)
            and receipt.get("status") == "saved"
            and bool(receipt.get("change")),
            "input_expired",
        )
        command = receipt.pop("change")
        action = command["action"]
        if action == "create":
            template = None
            if lineup := command.get("lineup"):
                template = await self.store.get(LineupTemplate, UUID(lineup["id"]))
                require(
                    template.group_id == group_id and template.version == lineup["version"],
                    "input_expired",
                )
            source = None
            if "source" in command:
                source = await self.template(group_id, UUID(command["source"]))
                require(await self.source_stamp(source, command) == command["stamp"], "conflict")
            fields = dict(command["fields"])
            concert = await Setlists(self.store, self.user).create(
                group_id, date.fromisoformat(fields.pop("date")), **fields
            )
            if template:
                await Staffing(self.store, self.user).apply_template(
                    concert.id, template.id, template.version
                )
            if source is not None:
                await self.copy_template(
                    source, concert, command, saved_lineup=template is not None
                )
        else:
            concert = await self.concert(group_id, command, deleted=action == "restore")
            if action == "delete":
                await self.trash.delete_concert(group_id, concert.id, command["fingerprint"])
            elif action == "restore":
                await self.trash.restore_concert(group_id, concert.id, command["epoch"])
            else:
                current = performance_input(concert).payload()
                details = ConcertDetails(self.store, self.user)
                for field, value in command["fields"].items():
                    if value == current[field]:
                        continue
                    if field == "duration" and value is not None:
                        value = duration_text(value)
                    await details.update(group_id, concert.id, DETAIL_FIELDS[field], value or "-")
        preview.command = json.dumps(receipt)
        return {"performance": str(concert.id), "past": concert.date < today(), "action": action}
