"""One band membership, with explicit consent for invitations and additional roles."""

from datetime import datetime, timedelta
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.member_boards import queue_member_board
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import (
    Group,
    Membership,
    Outbox,
    StaffContact,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.rules import AppError, require


class Team:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)

    async def invite(self, group_id: UUID, target: UUID, roles: list[str]) -> Membership:
        from setlist_bot.application.role_dictionary import RoleDictionary

        group = await self.groups.access(group_id, admin=True)
        await self.store.get(User, target)
        names = (await RoleDictionary(self.store).index()).local_names("\n".join(roles))
        require(bool(names), "input")
        rows = await self.store.find(Membership, group_id=group_id, user_id=target)
        member = (
            rows[0] if rows else Membership(group_id=group_id, user_id=target, status="invited")
        )
        if not rows:
            self.store.add(member)
        contacts = await self.store.find(
            StaffContact, group_id=group_id, user_id=target, permanent=True
        )
        current = {
            r.name for r in await self.store.get_many(StaffRole, {c.role_id for c in contacts})
        }
        names = [name for name in names if name not in current]
        if not names:
            return member
        if member.requested_roles and set(member.requested_roles) == set(names):
            await self.groups.decide_member(group_id, member.id, True, member.version)
            await self.notice(member, "team_joined")
            return member
        if member.invitation and member.invitation.get("roles") == names:
            try:
                await self.validate(member)
                return member
            except AppError:
                pass
        member.version += 1
        member.invitation = {
            "roles": names,
            "sender_id": str(self.user.id),
            "epoch": group.lifecycle_version,
            "expires_at": (now() + timedelta(days=30)).isoformat(),
        }
        if member.status != "active":
            member.status = "invited"
        await self.store.flush()
        await self.queue(member)
        record_activity(self.store, self.user, "team_invited", member.id)
        return member

    async def validate(self, member: Membership) -> Group:
        group = await self.store.get(Group, member.group_id, lock=True)
        data = member.invitation
        require(data is not None, "input_expired")
        assert data is not None
        require(
            not group.deleted_at
            and group.lifecycle_version == data["epoch"]
            and datetime.fromisoformat(data["expires_at"]) > now(),
            "input_expired",
        )
        require(bool(data.get("sender_id")), "input_expired")
        senders = await self.store.find(
            Membership,
            group_id=group.id,
            user_id=UUID(data["sender_id"]),
            status="active",
            role="admin",
        )
        require(bool(senders), "input_expired")
        return group

    async def queue(self, member: Membership) -> None:
        person = await self.store.get(User, member.user_id)
        self.store.add(
            Outbox(
                group_id=member.group_id,
                chat_id=person.telegram_id,
                support_kind="team",
                text="Team invitation",
                payload={"member_id": str(member.id), "version": member.version},
            )
        )

    async def respond(self, identifier: UUID, expected: int, accept: bool) -> Membership:
        member = await self.store.get(Membership, identifier)
        require(member.user_id == self.user.id, "forbidden")
        await self.validate(member)
        member = await self.store.get(Membership, identifier, lock=True)
        require(member.version == expected and member.invitation is not None, "input_expired")
        assert member.invitation is not None
        data = member.invitation
        if accept:
            from setlist_bot.application.staffing import Staffing

            member.status = "active"
            sender = await self.store.get(User, UUID(data["sender_id"]))
            service = Staffing(self.store, sender)
            for name in data["roles"]:
                role = await service.role(member.group_id, name)
                contact = await service.set_permanent(member.group_id, role.id, member.user_id)
                if data.get("legacy_priorities", {}).get(str(role.id)) is not None:
                    contact.priority = data["legacy_priorities"][str(role.id)]
            if member.requested_roles:
                member.requested_roles = [
                    r for r in member.requested_roles if r not in data["roles"]
                ] or None
        elif member.status != "active":
            member.status = "pending" if member.requested_roles else "left"
        member.invitation = None
        member.version += 1
        await queue_member_board(self.store, member.group_id, ensure=True)
        await self.notice(
            member, "team_joined" if accept else "team_declined", recipient=UUID(data["sender_id"])
        )
        record_activity(self.store, self.user, "team_response", member.id, accepted=accept)
        return member

    async def cancel(self, identifier: UUID, expected: int) -> Membership:
        member = await self.store.get(Membership, identifier)
        await self.groups.access(member.group_id, admin=True)
        member = await self.store.get(Membership, identifier, lock=True)
        require(member.version == expected and member.invitation is not None, "input_expired")
        member.invitation = None
        member.version += 1
        if member.status == "invited":
            member.status = "pending" if member.requested_roles else "left"
        await self.notice(member, "team_withdrawn")
        return member

    async def check_remove(self, member: Membership) -> Group:
        group = await self.groups.access(member.group_id, admin=member.user_id != self.user.id)
        require(member.status == "active", "input_expired")
        require(member.user_id != group.owner_id, "team_transfer_first")
        require(
            member.user_id == self.user.id
            or member.role != "admin"
            or group.owner_id == self.user.id,
            "owner_only",
        )
        return group

    async def remove(self, identifier: UUID, expected: int) -> Membership:
        member = await self.store.get(Membership, identifier)
        await self.check_remove(member)
        member = await self.store.get(Membership, identifier, lock=True)
        require(member.version == expected, "input_expired")
        member.status, member.role = "left", "member"
        member.invitation, member.requested_roles = None, None
        member.version += 1
        for contact in await self.store.find(
            StaffContact, group_id=member.group_id, user_id=member.user_id
        ):
            contact.permanent = False
            contact.version += 1
        await queue_member_board(self.store, member.group_id)
        await self.notice(member, "team_removed")
        record_activity(self.store, self.user, "team_left", member.id)
        return member

    async def notice(self, member: Membership, key: str, *, recipient: UUID | None = None) -> None:
        # Notification text is rendered at delivery, with a current group lifecycle check.
        user = await self.store.get(User, recipient or member.user_id)
        group = await self.store.get(Group, member.group_id)
        self.store.add(
            Outbox(
                group_id=group.id,
                chat_id=user.telegram_id,
                support_kind="team_note",
                text=key,
                payload={
                    "member_id": str(member.id),
                    "recipient_id": str(user.id),
                    "epoch": group.lifecycle_version,
                },
            )
        )
