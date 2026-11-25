"""Permanent team views and commands shared by the Mini App and browser."""

from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.member_boards import queue_member_board
from setlist_bot.application.ports import Store
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.team import Team
from setlist_bot.application.user_lookup import checked_target, known_user
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    Concert,
    GigInvitation,
    Group,
    Membership,
    StaffContact,
    StaffRole,
    User,
)
from setlist_bot.domain.rules import AppError, require
from setlist_bot.domain.telegram_identity import fallback_profile, person_caption

TEAM_SESSION = "miniapp_membership"
PERSONAL_ACTIONS = {"respond", "leave", "leave_preview"}
CONTACT_ACTIONS = {"role_remove", "role_visibility", "role_move", "role_priority"}


def person_name(person: User, member: Membership | None = None) -> str:
    return (
        person.profile.get("name")
        or (member.musical_role if member else "")
        or person.display_name
        or (f"@{person.username}" if person.username else "—")
    )


def text_field(body: dict[str, Any], field: str, limit: int) -> str:
    value = body.get(field)
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, "input")
    assert isinstance(value, str)
    return value.strip()


class WorkspaceTeam:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)
        self.team = Team(store, user)
        self.staffing = Staffing(store, user)

    async def own_membership(
        self, identifier: UUID, *, opening: bool = False
    ) -> tuple[Membership, Group]:
        member = await self.store.get(Membership, identifier)
        require(member.user_id == self.user.id, "forbidden")
        group = await self.store.get(Group, member.group_id, lock=True)
        member = await self.store.get(Membership, identifier, lock=True)
        require(group.deleted_at is None, "in_trash")
        if opening and member.status != "active":
            await self.team.validate(member)
        return member, group

    async def inbox(self) -> dict[str, Any]:
        invitations = []
        memberships = await self.store.find(Membership, user_id=self.user.id)
        for member in sorted(memberships, key=lambda row: row.group_id):
            if member.invitation is None:
                continue
            try:
                group = await self.team.validate(member, lock=False)
            except AppError as exc:
                if exc.code not in ("input_expired", "in_trash"):
                    raise
                continue
            assert member.invitation is not None
            invitations.append(
                {
                    "band": group.name,
                    "id": str(member.id),
                    "version": member.version,
                    "roles": member.invitation["roles"],
                    "already_member": member.status == "active",
                }
            )
        invitations.sort(key=lambda row: row["band"].casefold())
        return {"language": self.user.language, "invitations": invitations}

    async def view(self, group_id: UUID, page: int, limit: int) -> dict[str, Any]:
        group = await self.groups.access(group_id)
        own = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
        admin = own.role == "admin"
        owner = group.owner_id == self.user.id
        roles = {role.id: role.name for role in await self.store.find(StaffRole, group_id=group_id)}
        rows = (
            [
                row
                for row in await self.store.find(Membership, group_id=group_id)
                if row.status != "left" or row.invitation
            ]
            if admin
            else [own]
        )
        rows.sort(key=lambda row: (row.user_id != group.owner_id, row.id))
        selected = rows[page * limit : (page + 1) * limit] if admin else rows
        ids = {row.user_id for row in selected}
        people = {u.id: u for u in await self.store.get_many(User, ids)}
        filters = {} if admin else {"user_id": self.user.id}
        by_user: dict[UUID, list[dict[str, str]]] = {}
        for contact in await self.store.find(
            StaffContact, group_id=group_id, permanent=True, **filters
        ):
            if contact.user_id in ids:
                by_user.setdefault(contact.user_id, []).append(
                    {"id": str(contact.id), "name": roles[contact.role_id]}
                )
        return {
            "language": self.user.language,
            "band": group.name,
            "admin": admin,
            "owner": owner,
            "own": {"id": str(own.id), "version": own.version},
            "has_next": admin and len(rows) > (page + 1) * limit,
            "members": [
                {
                    "id": str(row.id),
                    "name": person_name(people[row.user_id], row),
                    "profile_url": fallback_profile(people[row.user_id]),
                    "profile_id": str(people[row.user_id].telegram_id)
                    if fallback_profile(people[row.user_id])
                    else None,
                    "status": row.status,
                    "right": "owner" if row.user_id == group.owner_id else row.role,
                    "roles": sorted(
                        by_user.get(row.user_id, []), key=lambda role: role["name"].casefold()
                    ),
                    "invitation": row.invitation["roles"] if row.invitation and admin else None,
                    "requested": row.requested_roles,
                    "version": row.version,
                    "self": row.id == own.id,
                    "removable": admin
                    and row.status == "active"
                    and row.user_id != group.owner_id
                    and (row.role != "admin" or owner)
                    and row.id != own.id,
                }
                for row in selected
            ],
        }

    async def contact(self, group_id: UUID, identifier: UUID) -> dict[str, Any]:
        group = await self.groups.access(group_id, admin=True)
        contact = await self.store.get(StaffContact, identifier)
        require(contact.group_id == group_id and contact.permanent, "not_found")
        role = await self.store.get(StaffRole, contact.role_id)
        ordered = await self.staffing.permanent_order(group_id, role.id)
        people = {
            u.id: u for u in await self.store.get_many(User, {row.user_id for row in ordered})
        }
        visible = [row for row in ordered if row.preference != "hidden"]
        position = visible.index(contact) if contact in visible else None
        return {
            "language": self.user.language,
            "band": group.name,
            "id": str(contact.id),
            "version": contact.version,
            "name": role.name,
            "performer": person_name(people[contact.user_id]),
            "priority": contact.priority,
            "hidden": position is None,
            "up": position is not None and position > 0,
            "down": position is not None and position + 1 < len(visible),
            "order": [
                {"name": person_name(people[row.user_id]), "hidden": row.preference == "hidden"}
                for row in ordered
            ],
        }

    async def recipient(self, group_id: UUID, reference: str) -> dict[str, Any]:
        await self.groups.access(group_id, admin=True)
        person = await known_user(self.store, reference)
        return {
            "id": str(person.id),
            "name": person_caption(person),
            "username": person.username,
            "profile_url": fallback_profile(person),
        }

    @staticmethod
    def command(body: dict[str, Any], *, personal: bool) -> dict[str, Any]:
        action = text_field(body, "action", 30)
        allowed = (
            PERSONAL_ACTIONS
            if personal
            else {
                "invite",
                "approve",
                "reject",
                "withdraw",
                "retry",
                "remove",
                "remove_preview",
                "grant",
                "revoke",
                "transfer",
                "request_roles",
                *CONTACT_ACTIONS,
            }
        )
        require(action in allowed, "input")
        result: dict[str, Any] = {"action": action}
        if action in ("invite", "request_roles"):
            result["roles"] = text_field(body, "roles", 4000)
            if action == "invite":
                result["reference"] = text_field(body, "reference", 64)
                result["target"] = str(UUID(text_field(body, "target", 36)))
            return result
        if action not in PERSONAL_ACTIONS:
            result["target"] = str(UUID(text_field(body, "target", 36)))
        version = body.get("version")
        require(type(version) is int, "input")
        result["version"] = version
        if action == "respond":
            require(type(body.get("accept")) is bool, "input")
            result["accept"] = body["accept"]
        if action == "role_move":
            require(type(body.get("step")) is int and body["step"] in (-1, 1), "input")
            result["step"] = body["step"]
        if action == "role_priority":
            require(type(body.get("priority")) is int and 0 <= body["priority"] <= 9999, "input")
            result["priority"] = body["priority"]
        if action == "transfer":
            result["confirmation"] = text_field(body, "confirmation", 200)
        return result

    async def bookings(self, member: Membership) -> list[dict[str, str]]:
        invites = await self.store.find(
            GigInvitation, group_id=member.group_id, user_id=member.user_id, status="confirmed"
        )
        events = await self.store.get_many(Concert, {row.concert_id for row in invites})
        current = today()
        return [
            {"title": concert.title, "date": concert.date.isoformat()}
            for concert in sorted(events, key=lambda row: (row.date, row.id))
            if concert.group_id == member.group_id
            and concert.deleted_at is None
            and concert.date >= current
        ]

    async def apply(
        self, group_id: UUID, command: dict[str, Any], personal_id: UUID | None
    ) -> dict[str, Any]:
        action = command["action"]
        if personal_id is not None:
            member, group = await self.own_membership(personal_id)
            require(group.id == group_id and member.version == command["version"], "input_expired")
            if action == "respond":
                await self.team.respond(member.id, member.version, command["accept"])
            elif action == "leave_preview":
                await self.team.check_remove(member)
                return {"bookings": await self.bookings(member)}
            else:
                require(action == "leave", "input")
                await self.team.remove(member.id, member.version)
            return {}
        group = await self.groups.access(group_id, admin=action != "request_roles")
        if action == "request_roles":
            await self.groups.request_roles(group_id, [command["roles"]])
            await queue_member_board(self.store, group_id, ensure=True)
            return {}
        if action == "invite":
            person = await checked_target(self.store, UUID(command["target"]), command["reference"])
            member = await self.team.invite(group_id, person.id, [command["roles"]])
            return {"invited": member.invitation is not None}
        identifier = UUID(command["target"])
        version = command["version"]
        if action in CONTACT_ACTIONS:
            row = await self.store.get(StaffContact, identifier)
            require(
                row.group_id == group_id and row.permanent and row.version == version,
                "input_expired",
            )
            if action == "role_move":
                await self.staffing.move_permanent(row.id, version, command["step"])
            elif action == "role_visibility":
                await self.staffing.contact(
                    group_id,
                    row.role_id,
                    row.user_id,
                    "normal" if row.preference == "hidden" else "hidden",
                )
            else:
                await self.staffing.change_contact(
                    row.id,
                    version,
                    remove=action == "role_remove",
                    priority=command.get("priority"),
                )
            return {}
        member = await self.store.get(Membership, identifier)
        require(member.group_id == group_id, "not_found")
        require(member.version == version, "input_expired")
        if action in ("approve", "reject"):
            await self.groups.decide_member(group_id, member.id, action == "approve", version)
        elif action == "retry":
            await self.team.retry(member.id, version)
        elif action == "withdraw":
            await self.team.cancel(member.id, version)
        elif action == "remove_preview":
            await self.team.check_remove(member)
            return {"bookings": await self.bookings(member)}
        elif action == "remove":
            require(member.user_id != self.user.id, "input")
            await self.team.remove(member.id, version)
        elif action in ("grant", "revoke"):
            await self.groups.administrator(group_id, member.id, action == "grant")
        else:
            require(action == "transfer", "input")
            await self.groups.transfer_ownership(
                group_id, member.id, str(group.owner_id), command["confirmation"]
            )
        return {}
