"""Owner-approved onboarding, role requests and atomic chat relocation.

RU: Telegram-владельца проверяет транспорт заново при нажатии. Эти сценарии
не доверяют user_id из callback: в сессии хранятся инициатор, исходная привязка
и срок. EN: Verified Telegram ownership is an explicit input, never callback data.
"""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import ChatBinding, Confirmation, Group, Membership, User, now
from setlist_bot.domain.rules import require


class Collectives:
    def __init__(self, store: Store, user: User, chat_id: int):
        self.store, self.user, self.chat_id = store, user, chat_id
        self.groups = Groups(store, user)

    def session(self, kind: str, data: dict[str, Any], *, minutes: int = 30) -> Confirmation:
        row = Confirmation(
            user_id=self.user.id,
            chat_id=self.chat_id,
            command=json.dumps({**data, "kind": kind}),
            expires_at=now() + timedelta(minutes=minutes),
        )
        self.store.add(row)
        return row

    async def load(self, identifier: UUID, kind: str) -> tuple[Confirmation, dict[str, Any]]:
        rows = await self.store.find(Confirmation, id=identifier)
        require(bool(rows), "input_expired")
        row = await self.store.get(Confirmation, identifier, lock=True)
        data = json.loads(row.command)
        require(
            row.expires_at > now()
            and data.get("kind") == kind
            and data.get("status", "pending") == "pending",
            "input_expired",
        )
        return row, data

    @staticmethod
    def finish(row: Confirmation, data: dict[str, Any], status: str) -> None:
        row.command = json.dumps({**data, "status": status})

    async def create(self, session: Confirmation, chat_owner_id: int | None) -> Group:
        require(chat_owner_id is not None, "chat_owner_unavailable")
        require(self.user.telegram_id == chat_owner_id, "chat_owner_only")
        data = json.loads(session.command)
        require(
            session.chat_id == self.chat_id
            and session.expires_at > now()
            and data.get("kind") == "create_group"
            and data.get("stage") == "review",
            "group_creation_expired",
        )
        initiator = await self.store.get(User, session.user_id)
        group = await self.groups.create(data["name"], self.chat_id)
        if initiator.id != self.user.id:
            self.store.add(
                Membership(group_id=group.id, user_id=initiator.id, role="admin", status="active")
            )
        await self.store.delete(session)
        return group

    async def role_request(self, group_id: UUID) -> tuple[Confirmation, bool]:
        group = await self.groups.access(group_id)
        require(group.owner_id is not None, "not_found")
        member = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
        require(member.role != "admin", "already_done")
        for row in await self.store.find(Confirmation, user_id=self.user.id):
            data = json.loads(row.command)
            if (
                data.get("kind") == "role_request"
                and data.get("group_id") == str(group_id)
                and data.get("status") == "pending"
                and row.expires_at > now()
            ):
                return row, False
        return self.session(
            "role_request",
            {
                "group_id": str(group_id),
                "group_epoch": group.lifecycle_version,
                "owner_id": str(group.owner_id),
                "status": "pending",
            },
            minutes=1440,
        ), True

    async def decide_role(self, identifier: UUID, accept: bool) -> tuple[User, Group]:
        # RU/EN: Lock the tenant before the request, matching the owner action lock order.
        probe = await self.store.get(Confirmation, identifier)
        group = await self.groups.access(UUID(json.loads(probe.command)["group_id"]), admin=True)
        row, data = await self.load(identifier, "role_request")
        require(
            self.chat_id == self.user.telegram_id
            and group.owner_id == self.user.id
            and str(group.owner_id) == data["owner_id"]
            and group.lifecycle_version == data["group_epoch"],
            "owner_only",
        )
        member = await self.store.find(
            Membership, group_id=group.id, user_id=row.user_id, status="active"
        )
        require(bool(member), "input_expired")
        if accept:
            await self.groups.administrator(group.id, member[0].id, True)
        self.finish(row, data, "accepted" if accept else "declined")
        return await self.store.get(User, row.user_id), group

    async def begin_transfer(self, group_id: UUID) -> Confirmation:
        group = await self.groups.access(group_id, admin=True)
        require(
            group.owner_id == self.user.id and self.chat_id == self.user.telegram_id, "owner_only"
        )
        bindings = await self.store.find(ChatBinding, group_id=group_id)
        require(bool(bindings), "not_found")
        # Only the most recently issued code remains valid.
        for old in await self.store.find(Confirmation, user_id=self.user.id):
            data = json.loads(old.command)
            if data.get("kind") == "chat_transfer" and data.get("group_id") == str(group_id):
                self.finish(old, data, "replaced")
        return self.session(
            "chat_transfer",
            {
                "group_id": str(group_id),
                "group_epoch": group.lifecycle_version,
                "owner_id": str(self.user.id),
                "old_chat_id": bindings[0].chat_id,
                "new_chat_id": None,
                "status": "pending",
            },
        )

    async def claim_transfer(self, identifier: UUID) -> tuple[Confirmation, Group]:
        require(self.chat_id < 0, "group_only")
        probe = await self.store.get(Confirmation, identifier)
        group = await self.groups.access(UUID(json.loads(probe.command)["group_id"]), admin=True)
        row, data = await self.load(identifier, "chat_transfer")
        require(
            row.user_id == self.user.id == group.owner_id
            and group.lifecycle_version == data["group_epoch"],
            "owner_only",
        )
        require(data["new_chat_id"] in (None, self.chat_id), "input_expired")
        await self.groups.check_creation_chat(self.chat_id)
        row.command = json.dumps({**data, "new_chat_id": self.chat_id})
        return row, group

    async def finish_transfer(self, identifier: UUID, chat_owner_id: int | None) -> Group:
        require(self.chat_id < 0, "group_only")
        require(chat_owner_id is not None, "chat_owner_unavailable")
        require(chat_owner_id == self.user.telegram_id, "chat_owner_only")
        probe = await self.store.get(Confirmation, identifier)
        data = json.loads(probe.command)
        group = await self.store.get(Group, UUID(data["group_id"]), lock=True)
        row, data = await self.load(identifier, "chat_transfer")
        require(
            data["new_chat_id"] == self.chat_id
            and group.deleted_at is None
            and group.lifecycle_version == data["group_epoch"]
            and str(group.owner_id) == data["owner_id"] == str(row.user_id),
            "input_expired",
        )
        # A revoked owner membership also invalidates the authorization to move.
        members = await self.store.find(
            Membership, group_id=group.id, user_id=row.user_id, status="active", role="admin"
        )
        require(bool(members), "owner_only")
        await self.groups.check_creation_chat(self.chat_id)
        binding = (await self.store.find(ChatBinding, group_id=group.id))[0]
        require(binding.chat_id == data["old_chat_id"], "input_expired")
        binding.chat_id = self.chat_id
        self.finish(row, data, "accepted")
        record_activity(self.store, self.user, "group_chat_transferred", group.id)
        return group
