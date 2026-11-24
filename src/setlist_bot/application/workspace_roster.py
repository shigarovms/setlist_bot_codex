"""Performance roster, with read-only availability reviews and explicit delivery."""

import json
from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.staffing import OPEN, Staffing
from setlist_bot.application.user_lookup import checked_target
from setlist_bot.application.workspace_replacements import (
    active_replacements,
    replacement_data,
    replacement_source,
)
from setlist_bot.application.workspace_staffing import WorkspaceStaffing, lineup_input
from setlist_bot.application.workspace_team import person_name, text_field
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    Concert,
    Confirmation,
    GigInvitation,
    GigSlot,
    Group,
    LineupTemplate,
    Membership,
    StaffContact,
    StaffReplacement,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.rules import require

ROSTER_KIND = "miniapp_roster"
SLOT_ACTIONS = {"candidates", "remove", "needed"}
INVITE_ACTIONS = {"remind", "cancel"}
REPLACEMENT_ACTIONS = {"request_replacement", "cancel_replacement", "approve_replacement"}


def status(busy: int, tentative: int) -> str:
    return "busy" if busy else "tentative" if tentative else "unknown"


class WorkspaceRoster:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.staffing = Staffing(store, user)
        self.groups = Groups(store, user)

    async def event(self, group_id: UUID, event_id: UUID, *, admin: bool) -> tuple[Concert, Group]:
        group = await self.groups.access(group_id, admin=admin)
        event = await self.store.get(Concert, event_id)
        require(event.group_id == group.id and event.deleted_at is None, "not_found")
        if not admin:
            await self.groups.check_performance_visibility(group_id, event_id)
        return event, group

    async def slots(self, event: Concert) -> list[GigSlot]:
        return sorted(
            await self.store.find(
                GigSlot, group_id=event.group_id, concert_id=event.id, removed=False
            ),
            key=lambda slot: slot.position,
        )

    async def active(self, event: Concert, group: Group) -> list[GigInvitation]:
        return [
            invite
            for invite in await self.store.roster_invitations(group.id, event.id)
            if invite.status in OPEN | {"confirmed"} and self.staffing.valid(invite, event, group)
        ]

    async def view(self, group_id: UUID, event_id: UUID) -> dict[str, Any]:
        event, group = await self.event(group_id, event_id, admin=False)
        member = (await self.store.find(Membership, group_id=group.id, user_id=self.user.id))[0]
        admin = member.role == "admin"
        rows = await self.slots(event)
        invitations = await self.store.roster_invitations(group.id, event.id)
        active = [
            row
            for row in invitations
            if row.status in OPEN | {"confirmed"} and self.staffing.valid(row, event, group)
        ]
        pending = {row.source_id: row for row in await active_replacements(self.store, event)}
        replacement_ids = {row.id for row in pending.values()}
        shown = [
            row
            for row in active
            if row.status == "confirmed"
            or admin
            and (row.replacement_id is None or row.replacement_id in replacement_ids)
        ]
        ids = {row.user_id for row in shown}
        if admin:
            ids.update(slot.preferred_user_id for slot in rows if slot.preferred_user_id)
        people = {person.id: person for person in await self.store.get_many(User, ids)}
        roles = {
            role.id: role.name
            for role in await self.store.get_many(StaffRole, {slot.role_id for slot in rows})
        }
        by_slot: dict[UUID, list[GigInvitation]] = {}
        for row in sorted(shown, key=lambda r: (r.status != "confirmed", r.created_at, r.id)):
            by_slot.setdefault(row.slot_id, []).append(row)
        templates = []
        if admin:
            templates = sorted(
                await self.store.find(LineupTemplate, group_id=group.id),
                key=lambda row: (row.name.casefold(), row.id),
            )
        return {
            "language": self.user.language,
            "band": group.name,
            "title": event.title,
            "date": event.date.isoformat(),
            "name": event.lineup_name,
            "version": event.lineup_version,
            "admin": admin,
            "upcoming": event.date >= today(),
            "can_prepare": admin
            and all(slot.required for slot in rows)
            and not any(r.status in OPEN | {"confirmed"} for r in invitations),
            "can_send_defaults": admin and bool(self.default_targets(rows, active)),
            "templates": [
                {"id": str(row.id), "name": row.name, "version": row.version} for row in templates
            ],
            "slots": [
                {
                    "id": str(slot.id),
                    "version": slot.version,
                    "role": roles[slot.role_id],
                    "required": slot.required,
                    "preferred": (
                        {
                            "name": person_name(people[slot.preferred_user_id]),
                            "reference": str(people[slot.preferred_user_id].telegram_id),
                        }
                        if admin and slot.preferred_user_id
                        else None
                    ),
                    "invitations": [
                        {
                            "id": str(row.id),
                            "name": person_name(people[row.user_id]),
                            "status": row.status,
                            "version": row.version,
                            "replacement": row.replacement_id in replacement_ids,
                            "substituted": row.status == "confirmed"
                            and row.replacement_id is not None,
                            "needs_replacement": row.id in pending,
                            "request": replacement_data(pending[row.id])
                            if admin and row.id in pending
                            else None,
                        }
                        for row in by_slot.get(slot.id, [])
                    ],
                }
                for slot in rows
            ],
        }

    @staticmethod
    def command(body: dict[str, Any]) -> dict[str, Any]:
        action = text_field(body, "action", 30)
        require(
            action
            in {
                "prepare",
                "add",
                "apply_template",
                "review",
                "send",
                *SLOT_ACTIONS,
                *INVITE_ACTIONS,
                *REPLACEMENT_ACTIONS,
            },
            "input",
        )
        if action == "send":
            force = body.get("force", False)
            require(type(force) is bool, "input")
            return {
                "action": action,
                "preview": str(UUID(text_field(body, "preview", 36))),
                "force": force,
            }
        version = body.get("version")
        require(type(version) is int, "input")
        command: dict[str, Any] = {"action": action, "version": version}
        if action in SLOT_ACTIONS | INVITE_ACTIONS | REPLACEMENT_ACTIONS | {"apply_template"} or (
            action == "review" and body.get("target")
        ):
            command["target"] = str(UUID(text_field(body, "target", 36)))
            require(type(body.get("expected")) is int, "input")
            command["expected"] = body["expected"]
        if action == "needed":
            require(type(body.get("required")) is bool, "input")
            command["required"] = body["required"]
        if action in ("prepare", "add"):
            command["rows"] = lineup_input(body, preview=False)
            require(action != "add" or len(command["rows"]) == 1, "input")
        if action == "review" and "target" in command:
            command["person"] = str(UUID(text_field(body, "person", 36)))
            if body.get("reference"):
                command["reference"] = text_field(body, "reference", 64)
        if action in ("review", "candidates") and body.get("replacement"):
            require("target" in command, "input")
            command["replacement"] = str(UUID(text_field(body, "replacement", 36)))
        return command

    async def open_replacement(
        self, event: Concert, group: Group, identifier: UUID
    ) -> tuple[StaffReplacement, GigInvitation]:
        pending, source = await replacement_source(self.store, identifier, group.id, event.id)
        require(
            pending.status == "pending"
            and pending.expires_at > now()
            and source.status == "confirmed"
            and self.staffing.valid(source, event, group)
            and event.date >= today(),
            "input_expired",
        )
        return pending, source

    async def slot(self, event: Concert, command: dict[str, Any]) -> GigSlot:
        slot = await self.store.get(GigSlot, UUID(command["target"]))
        require(
            slot.group_id == event.group_id
            and slot.concert_id == event.id
            and not slot.removed
            and slot.version == command["expected"],
            "input_expired",
        )
        return slot

    @staticmethod
    def default_targets(
        slots: list[GigSlot], active: list[GigInvitation]
    ) -> list[tuple[GigSlot, UUID]]:
        filled = {row.slot_id for row in active if row.status == "confirmed"}
        waiting = {(row.slot_id, row.user_id) for row in active}
        return [
            (slot, slot.preferred_user_id)
            for slot in slots
            if slot.required
            and slot.preferred_user_id is not None
            and slot.id not in filled
            and (slot.id, slot.preferred_user_id) not in waiting
        ]

    async def review(self, event: Concert, group: Group, command: dict[str, Any]) -> dict[str, Any]:
        require(event.date >= today(), "input_expired")
        active = await self.active(event, group)
        replacement = source = None
        if "target" in command:
            slot = await self.slot(event, command)
            person = UUID(command["person"])
            if "reference" in command:
                await checked_target(self.store, person, command["reference"])
            else:
                candidates = await self.staffing.candidates(slot.id)
                require(
                    person == self.user.id or any(u.id == person for _, u, *_ in candidates),
                    "input_expired",
                )
            require(slot.required, "input_expired")
            if "replacement" in command:
                replacement, source = await self.open_replacement(
                    event, group, UUID(command["replacement"])
                )
                require(source.slot_id == slot.id and source.user_id != person, "input_expired")
                require(
                    not any(
                        row.replacement_id == replacement.id and row.user_id == person
                        for row in active
                    ),
                    "staff_filled",
                )
            else:
                require(
                    not any(
                        inv.slot_id == slot.id
                        and (inv.status == "confirmed" or inv.user_id == person)
                        for inv in active
                    ),
                    "staff_filled",
                )
            targets = [(slot, person)]
        else:
            targets = self.default_targets(await self.slots(event), active)
        require(bool(targets), "staff_filled")
        hidden = {
            (row.role_id, row.user_id)
            for row in await self.store.find(StaffContact, group_id=group.id, preference="hidden")
        }
        require(
            not any((slot.role_id, person) in hidden for slot, person in targets), "staff_hidden"
        )
        people = {
            person.id: person for person in await self.store.get_many(User, {p for _, p in targets})
        }
        roles = {
            role.id: role.name
            for role in await self.store.get_many(StaffRole, {slot.role_id for slot, _ in targets})
        }
        conflicts = await self.store.staffing_conflicts(set(people), event.date, event.id)
        return {
            "review": [
                {
                    "role": roles[slot.role_id],
                    "person": person_name(people[person]),
                    "self": self.user.id == person,
                    "status": status(*conflicts[person]),
                }
                for slot, person in targets
            ],
            "delivery": {
                "version": event.lineup_version,
                "date": event.date.isoformat(),
                "targets": [
                    {"slot": str(slot.id), "version": slot.version, "person": str(person)}
                    for slot, person in targets
                ],
                "availability": {str(person): status(*conflicts[person]) for person in people},
                **(
                    {"replacement": str(replacement.id), "source": str(source.user_id)}
                    if replacement is not None and source is not None
                    else {}
                ),
            },
            "replacement": replacement is not None,
        }

    async def send_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        row = await self.store.get(Confirmation, UUID(request["roster"]["preview"]))
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and row.expires_at > now()
            and data.get("kind") == ROSTER_KIND
            and data.get("status") == "saved"
            and bool(data.get("delivery"))
            and all(data.get(key) == request[key] for key in ("session", "group_id", "concert_id")),
            "input_expired",
        )
        return dict(data["delivery"])

    async def send(
        self, event: Concert, command: dict[str, Any], plan: dict[str, Any]
    ) -> dict[str, Any]:
        # The worker acquired the entire person-lock set before resolving the band scope.
        require(
            event.lineup_version == plan["version"] and event.date.isoformat() == plan["date"],
            "input_expired",
        )
        people = {UUID(row["person"]) for row in plan["targets"]}
        conflicts = await self.store.staffing_conflicts(people, event.date, event.id)
        availability = {str(person): status(*conflicts[person]) for person in people}
        require(availability == plan["availability"], "conflict")
        require("busy" not in availability.values() or command["force"], "staff_busy")
        replacement = UUID(plan["replacement"]) if plan.get("replacement") else None
        for target in plan["targets"]:
            invite = await self.staffing.invite(
                UUID(target["slot"]),
                UUID(target["person"]),
                target["version"],
                force=command["force"],
                replacement_id=replacement,
            )
            if replacement is not None and invite.status == "confirmed":
                return {"replacement_confirmed": True}
        return {
            "recipients": len(people - {self.user.id}),
            "assigned_self": self.user.id in people,
        }

    async def delivery_people(self, request: dict[str, Any]) -> set[UUID]:
        command = request["roster"]
        action = command["action"]
        if action == "send":
            plan = await self.send_plan(request)
            people = {UUID(row["person"]) for row in plan["targets"]}
            if source := plan.get("source"):
                people.add(UUID(source))
            return people
        if action not in REPLACEMENT_ACTIONS:
            return set()
        group_id, event_id = UUID(request["group_id"]), UUID(request["concert_id"])
        identifier = UUID(command["target"])
        if action == "cancel_replacement":
            _, source_row = await replacement_source(self.store, identifier, group_id, event_id)
            return {source_row.user_id}
        invite = await self.store.get(GigInvitation, identifier)
        require(invite.group_id == group_id and invite.concert_id == event_id, "not_found")
        if action == "request_replacement":
            return {invite.user_id}
        require(invite.replacement_id is not None, "input_expired")
        assert invite.replacement_id is not None
        _, source_row = await replacement_source(
            self.store, invite.replacement_id, group_id, event_id
        )
        return {invite.user_id, source_row.user_id}

    async def apply_replacement(
        self, event: Concert, group: Group, command: dict[str, Any]
    ) -> dict[str, Any]:
        action, identifier = command["action"], UUID(command["target"])
        if action == "cancel_replacement":
            pending, _ = await self.open_replacement(event, group, identifier)
            await self.staffing.cancel_request(pending.id, command["expected"])
            return {}
        rows = await self.store.roster_invitations(group.id, event.id)
        invite = next((row for row in rows if row.id == identifier), None)
        require(invite is not None and invite.version == command["expected"], "input_expired")
        assert invite is not None
        if action == "request_replacement":
            await self.staffing.request_replacement(invite.id, by_admin=True)
            return {}
        await self.staffing.approve_replacement(invite.id, invite.version)
        return {"replacement_confirmed": True}

    async def apply(
        self, group_id: UUID, event_id: UUID, request: dict[str, Any]
    ) -> dict[str, Any]:
        event, group = await self.event(group_id, event_id, admin=True)
        command = request["roster"]
        action = command["action"]
        if action == "send":
            return await self.send(event, command, await self.send_plan(request))
        require(event.lineup_version == command["version"], "input_expired")
        if action == "review":
            return await self.review(event, group, command)
        if action == "candidates":
            slot = await self.slot(event, command)
            people = await self.staffing.candidates(slot.id)
            excluded: set[UUID] = set()
            if "replacement" in command:
                pending, source = await self.open_replacement(
                    event, group, UUID(command["replacement"])
                )
                require(source.slot_id == slot.id, "input_expired")
                excluded = {
                    source.user_id,
                    *(
                        row.user_id
                        for row in await self.active(event, group)
                        if row.replacement_id == pending.id
                    ),
                }
            return {
                "candidates": [
                    {
                        "id": str(person.id),
                        "name": person_name(person),
                        "status": status(busy, tentative),
                    }
                    for _, person, busy, tentative in people
                    if person.id not in excluded
                ],
                "self": {"id": str(self.user.id), "name": person_name(self.user)}
                if self.user.id not in excluded
                else None,
            }
        if action in REPLACEMENT_ACTIONS:
            return await self.apply_replacement(event, group, command)
        if action in ("prepare", "add"):
            if action == "prepare":
                require(all(slot.required for slot in await self.slots(event)), "input_expired")
            values = await WorkspaceStaffing(self.store, self.user).resolve(
                group_id, command["rows"]
            )
            if action == "prepare":
                await self.staffing.replace_uninvited_lineup(
                    event.id, command["version"], event.lineup_name, values
                )
            else:
                value = values[0]
                slot = await self.staffing.add_slot(event.id, UUID(value["role_id"]))
                if value["user_id"]:
                    slot.preferred_user_id = UUID(value["user_id"])
                    await self.staffing.contact(group_id, slot.role_id, slot.preferred_user_id)
        elif action == "apply_template":
            await self.staffing.apply_template(
                event.id,
                UUID(command["target"]),
                command["expected"],
                replace=True,
                event_version=command["version"],
            )
        elif action in INVITE_ACTIONS:
            invitation = await self.store.get(GigInvitation, UUID(command["target"]))
            require(
                invitation.group_id == group_id
                and invitation.concert_id == event.id
                and self.staffing.valid(invitation, event, group),
                "input_expired",
            )
            if action == "remind":
                await self.staffing.remind(invitation.id, command["expected"])
            else:
                await self.staffing.cancel_invitation(invitation.id, command["expected"])
        else:
            slot = await self.slot(event, command)
            if action == "remove":
                await self.staffing.remove_slot(slot.id, slot.version)
            else:
                await self.staffing.needed(slot.id, slot.version, command["required"])
        return {}
