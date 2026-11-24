"""Read and answer one's own performance invitations, including as a guest."""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Document, Setlists
from setlist_bot.application.staffing import OPEN, Staffing
from setlist_bot.application.workspace_replacements import (
    active_replacements,
    replacement_data,
    replacement_source,
)
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    Concert,
    Confirmation,
    GigInvitation,
    GigSlot,
    Group,
    Setlist,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.rules import flat_content, is_pause, require, totals

GIG_SESSION = "miniapp_gig"
GIG_REPLY = "miniapp_gig_reply"
GIG_REPLACEMENT = "miniapp_gig_replacement"
REPLYABLE = {"pending", "conditional", "reconfirm"}


@dataclass(frozen=True)
class InvitationScope:
    invitation: GigInvitation
    event: Concert
    group: Group


class WorkspaceGigs:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.staffing = Staffing(store, user)

    async def own(self, identifier: UUID) -> GigInvitation:
        rows = await self.store.personal_invitations(self.user.id, identifier)
        require(bool(rows), "forbidden")
        return rows[0]

    async def scope(self, identifier: UUID, *, active: bool = True) -> InvitationScope:
        invitation = await self.own(identifier)
        group = await self.store.get(Group, invitation.group_id, lock=True)
        invitation = await self.own(identifier)
        event = await self.store.get(Concert, invitation.concert_id)
        require(event.group_id == group.id and invitation.group_id == group.id, "forbidden")
        if active:
            require(
                invitation.status in OPEN | {"confirmed"}
                and self.staffing.valid(invitation, event, group),
                "input_expired",
            )
        return InvitationScope(invitation, event, group)

    async def confirmed(self, identifier: UUID) -> InvitationScope:
        scope = await self.scope(identifier)
        require(scope.invitation.status == "confirmed", "forbidden")
        return scope

    async def shared(self, scope: InvitationScope) -> tuple[Document, int, bool]:
        require(scope.invitation.status == "confirmed", "forbidden")
        rows = await self.store.find(Setlist, group_id=scope.group.id, concert_id=scope.event.id)
        require(bool(rows), "not_found")
        return await Setlists(self.store, self.user)._read_shared(rows[0])

    async def listing(self, section: str, page: int, size: int) -> dict[str, Any]:
        invitations = await self.store.personal_invitations(self.user.id)
        events = {
            row.id: row
            for row in await self.store.get_many(Concert, {row.concert_id for row in invitations})
        }
        groups = {
            row.id: row
            for row in await self.store.get_many(Group, {row.group_id for row in invitations})
        }
        current = today()
        selected = []
        for row in invitations:
            event, group = events.get(row.concert_id), groups.get(row.group_id)
            if not event or not group or event.group_id != group.id:
                continue
            active = row.status in OPEN | {"confirmed"} and self.staffing.valid(row, event, group)
            matching = (
                section == ("confirmed" if row.status == "confirmed" else "invitations")
                if active
                else section == "history"
            )
            if matching:
                selected.append((row, event, group))
        selected.sort(
            key=lambda item: (
                item[0].date < current,
                item[0].date.toordinal() * (1 if item[0].date >= current else -1),
                item[0].id,
            )
        )
        shown = selected[page * size : (page + 1) * size]
        slots = {
            slot.id: slot
            for slot in await self.store.get_many(GigSlot, {r.slot_id for r, _, _ in shown})
        }
        roles = {
            role.id: role.name
            for role in await self.store.get_many(StaffRole, {s.role_id for s in slots.values()})
        }
        return {
            "language": self.user.language,
            "has_next": len(selected) > (page + 1) * size,
            "invitations": [
                {
                    "id": str(row.id),
                    "date": row.date.isoformat(),
                    "band": group.name,
                    "title": event.title if section != "history" else "",
                    "city": event.city if section != "history" else "",
                    "role": roles[slots[row.slot_id].role_id],
                    "status": "inactive"
                    if section == "history" and row.status in OPEN | {"confirmed"}
                    else row.status,
                }
                for row, event, group in shown
                if row.slot_id in slots
            ],
        }

    async def card(self, identifier: UUID) -> dict[str, Any]:
        scope = await self.scope(identifier)
        row, event, group = scope.invitation, scope.event, scope.group
        slot = await self.store.get(GigSlot, row.slot_id)
        require(slot.concert_id == event.id and slot.group_id == group.id, "forbidden")
        role = await self.store.get(StaffRole, slot.role_id)
        upcoming = event.date >= today()
        answers = []
        if upcoming and row.status in REPLYABLE:
            answers = ["yes", "no"] if row.replacement_id else ["yes", "no", "conditional"]
        elif upcoming and row.status == "accepted":
            answers = ["no"]
        replacements = []
        sources: list[dict[str, Any]] = []
        if upcoming and row.status == "confirmed":
            replacements = await active_replacements(self.store, event, source_id=row.id)
        elif upcoming and row.status == "conditional":
            sources = await self.replacement_sources(row)
        return {
            "language": self.user.language,
            "band": group.name,
            "invitation": {
                "id": str(row.id),
                "version": row.version,
                "title": event.title,
                "date": event.date.isoformat(),
                "city": event.city,
                "type": event.performance_type,
                "info": event.extra_info,
                "role": role.name,
                "status": row.status,
                "answers": answers,
                "setlist": row.status == "confirmed",
                "can_request_replacement": upcoming
                and row.status == "confirmed"
                and not replacements,
                "replacement": replacement_data(replacements[0]) if replacements else None,
                "replacement_sources": sources,
            },
        }

    async def replacement_sources(self, target: GigInvitation) -> list[dict[str, Any]]:
        invites = [
            row
            for row in await self.store.personal_invitations(self.user.id)
            if row.status == "confirmed"
            and row.date == target.date
            and row.concert_id != target.concert_id
        ]
        events = {
            row.id: row
            for row in await self.store.get_many(Concert, {r.concert_id for r in invites})
        }
        groups = {
            row.id: row for row in await self.store.get_many(Group, {r.group_id for r in invites})
        }
        options = []
        for row in sorted(invites, key=lambda item: item.id):
            event, group = events.get(row.concert_id), groups.get(row.group_id)
            if not event or not group or not self.staffing.valid(row, event, group):
                continue
            pending = await active_replacements(self.store, event, source_id=row.id)
            request = next((item for item in pending if item.target_id == target.id), None)
            options.append(
                {
                    "id": str(row.id),
                    "version": row.version,
                    "band": group.name,
                    "title": event.title,
                    "can_request": not pending,
                    "replacement": replacement_data(request) if request else None,
                }
            )
        return options

    async def setlist(self, identifier: UUID) -> dict[str, Any]:
        scope = await self.confirmed(identifier)
        snapshot, version, approved = await self.shared(scope)
        items = flat_content(snapshot)["blocks"][0]["items"]
        return {
            "language": self.user.language,
            "band": scope.group.name,
            "title": scope.event.title,
            "date": scope.event.date.isoformat(),
            "version": version,
            "approved": approved,
            "seconds": totals(snapshot)[2],
            "entries": [
                {
                    "pause": is_pause(item),
                    "title": item.get("pause_label") if is_pause(item) else item["title"],
                    "seconds": item["duration_seconds"],
                }
                for item in items
            ],
        }

    @staticmethod
    def command(body: dict[str, Any]) -> dict[str, Any]:
        answer = body.get("answer")
        version = body.get("version")
        previous = body.get("busy_receipt")
        require(answer in ("yes", "no", "conditional") and type(version) is int, "input")
        require(previous is None or (answer == "yes" and isinstance(previous, str)), "input")
        assert previous is None or isinstance(previous, str)
        return {
            "answer": answer,
            "version": version,
            "busy_receipt": str(UUID(previous)) if previous else None,
        }

    async def reply(self, invitation_id: UUID, request: dict[str, Any]) -> dict[str, Any]:
        command = request["gig_reply"]
        answer, version, previous = command["answer"], command["version"], command["busy_receipt"]
        if previous:
            row = await self.store.get(Confirmation, UUID(previous))
            failed = json.loads(row.command)
            require(
                row.user_id == self.user.id
                and row.expires_at > now()
                and failed.get("kind") == GIG_REPLY
                and failed.get("status") == "failed"
                and failed.get("result", {}).get("error") == "staff_busy"
                and failed.get("gig_reply")
                == {"answer": "yes", "version": version, "busy_receipt": None}
                and all(
                    failed.get(key) == request.get(key)
                    for key in ("session", "group_id", "concert_id")
                ),
                "input_expired",
            )
        invitation = await self.staffing.respond(
            invitation_id, version, answer, force=bool(previous)
        )
        return {"invitation_status": invitation.status}

    @staticmethod
    def replacement_command(body: dict[str, Any]) -> dict[str, Any]:
        action, version = body.get("action"), body.get("version")
        require(action in ("request", "cancel") and type(version) is int, "input")
        result = {"action": action, "version": version}
        if action == "request":
            target = body.get("target")
            require(target is None or isinstance(target, str), "input")
            if target is not None:
                result["target"] = str(UUID(target))
        else:
            replacement, expected = body.get("replacement"), body.get("expected")
            require(isinstance(replacement, str) and type(expected) is int, "input")
            result.update(replacement=str(UUID(replacement)), expected=expected)
        return result

    async def replace(self, invitation_id: UUID, command: dict[str, Any]) -> dict[str, Any]:
        scope = await self.confirmed(invitation_id)
        source = scope.invitation
        require(
            source.version == command["version"] and scope.event.date >= today(), "input_expired"
        )
        if command["action"] == "cancel":
            pending, _ = await replacement_source(
                self.store, UUID(command["replacement"]), scope.group.id, scope.event.id
            )
            require(pending.source_id == source.id and pending.expires_at > now(), "input_expired")
            await self.staffing.cancel_request(pending.id, command["expected"])
            return {"replacement_status": "cancelled"}
        target_id = None
        if "target" in command:
            target = await self.own(UUID(command["target"]))
            # Keep the conditional booking read-only here, including across bands.
            require(target.date == source.date, "input_expired")
            target_id = target.id
        pending = await self.staffing.request_replacement(source.id, target_id)
        require(pending.target_id == target_id, "input_expired")
        return {"replacement_status": "pending", "replacement": replacement_data(pending)}
