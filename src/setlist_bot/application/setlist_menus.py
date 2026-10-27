"""Version-bound cards and administrator requests in existing service sessions."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    Membership,
    User,
    now,
)
from setlist_bot.domain.rules import require

CARD = "setlist_menu"
REQUEST = "setlist_request"


class SetlistMenus:
    def __init__(self, store: Store, user: User, chat_id: int) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id

    @staticmethod
    def data(row: Confirmation) -> dict[str, Any]:
        return dict(json.loads(row.command))

    @staticmethod
    def write(row: Confirmation, data: dict[str, Any]) -> None:
        row.command = json.dumps(data, ensure_ascii=False)

    def create(self, data: dict[str, Any]) -> Confirmation:
        row = Confirmation(
            user_id=self.user.id,
            chat_id=self.chat_id,
            command="",
            expires_at=now() + timedelta(days=30),
        )
        self.write(row, {**data, "kind": CARD, "message_ids": []})
        self.store.add(row)
        return row

    async def validate(self, data: dict[str, Any], *, fresh: bool = False) -> None:
        group_id, concert_id = UUID(data["group_id"]), UUID(data["concert_id"])
        group = await Groups(self.store, self.user).access(group_id)
        concert = await self.store.get(Concert, concert_id)
        require(
            concert.group_id == group_id
            and concert.deleted_at is None
            and group.lifecycle_version == data["group_epoch"]
            and concert.lifecycle_version == data["concert_epoch"],
            "menu_expired",
        )
        if fresh:
            snapshot, version, approved = await Setlists(self.store, self.user).read(
                group_id, concert_id, private=data["personal"]
            )
            require(version == data["version"] and approved == data["approved"], "menu_expired")
            if not approved:
                require(snapshot == data["snapshot"], "menu_expired")

    async def load(
        self, identifier: UUID, message_id: int | None = None
    ) -> tuple[Confirmation, dict[str, Any]]:
        rows = await self.store.find(Confirmation, id=identifier)
        require(bool(rows), "menu_expired")
        row = rows[0]
        data = self.data(row)
        require(
            data.get("kind") == CARD
            and row.chat_id == self.chat_id
            and row.expires_at > now()
            and not data.get("closed")
            and (data["owner_id"] is None or data["owner_id"] == str(self.user.id))
            and (message_id is None or message_id in data["message_ids"]),
            "menu_expired",
        )
        await self.validate(data)
        if data["owner_id"] is None:
            require(
                bool(
                    await self.store.find(
                        ChatBinding, chat_id=self.chat_id, group_id=UUID(data["group_id"])
                    )
                ),
                "menu_expired",
            )
        return row, data

    async def request(
        self, card: Confirmation, data: dict[str, Any], operation: str
    ) -> tuple[Confirmation, User, bool]:
        require(not data["personal"] and self.chat_id < 0, "group_only")
        await self.validate(data, fresh=True)
        require(
            operation in ("approve", "reopen") and data["approved"] == (operation == "reopen"),
            "menu_expired",
        )
        if operation == "approve":
            require(any(block["items"] for block in data["snapshot"]["blocks"]), "empty_setlist")
        admins = await self.store.find(
            Membership, group_id=UUID(data["group_id"]), role="admin", status="active"
        )
        require(bool(admins), "forbidden")
        admin = await self.store.get(User, admins[0].user_id)
        for row in await self.store.find(Confirmation, user_id=admin.id, chat_id=admin.telegram_id):
            pending = self.data(row)
            if (
                pending.get("kind") == REQUEST
                and pending.get("status") == "pending"
                and row.expires_at > now()
                and pending["requester_id"] == str(self.user.id)
                and pending["group_id"] == data["group_id"]
                and pending["concert_id"] == data["concert_id"]
                and pending["version"] == data["version"]
                and pending["operation"] == operation
                and pending["group_epoch"] == data["group_epoch"]
                and pending["concert_epoch"] == data["concert_epoch"]
            ):
                return row, admin, False
        row = Confirmation(
            user_id=admin.id,
            chat_id=admin.telegram_id,
            command="",
            expires_at=now() + timedelta(days=1),
        )
        self.write(
            row,
            {
                **data,
                "kind": REQUEST,
                "message_ids": [],
                "status": "pending",
                "operation": operation,
                "source_chat_id": self.chat_id,
                "card_id": str(card.id),
                "requester_id": str(self.user.id),
            },
        )
        self.store.add(row)
        return row, admin, True

    async def decide(self, identifier: UUID, accept: bool) -> dict[str, Any]:
        rows = await self.store.find(
            Confirmation, id=identifier, user_id=self.user.id, chat_id=self.chat_id
        )
        require(bool(rows), "menu_expired")
        row = rows[0]
        data = self.data(row)
        require(
            data.get("kind") == REQUEST
            and data["status"] == "pending"
            and row.expires_at > now()
            and self.chat_id == self.user.telegram_id,
            "menu_expired",
        )
        await Groups(self.store, self.user).access(UUID(data["group_id"]), admin=True)
        await self.validate(data, fresh=True)
        require(
            bool(
                await self.store.find(
                    Membership,
                    group_id=UUID(data["group_id"]),
                    user_id=UUID(data["requester_id"]),
                    status="active",
                )
            ),
            "menu_expired",
        )
        if accept:
            service = Setlists(self.store, self.user)
            operation = service.approve if data["operation"] == "approve" else service.reopen
            await operation(UUID(data["group_id"]), UUID(data["concert_id"]), data["version"])
        data["status"] = "accepted" if accept else "rejected"
        self.write(row, data)
        return data
