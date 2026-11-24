"""Delete and recover bands using the same lifecycle rules as Telegram."""

import json
from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.trash import RETENTION, Trash
from setlist_bot.domain.entities import Confirmation, Group, Membership, User, now
from setlist_bot.domain.rules import require

BAND_TRASH_KIND = "miniapp_band_trash"


class WorkspaceBandTrash:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)
        self.trash = Trash(store, user)

    async def view(self, page: int, limit: int) -> dict[str, Any]:
        memberships = await self.store.find(
            Membership, user_id=self.user.id, role="admin", status="active"
        )
        groups = await self.store.get_many(Group, {row.group_id for row in memberships})
        cutoff = now()
        deleted = sorted(
            (
                (group.deleted_at + RETENTION, group)
                for group in groups
                if group.deleted_at is not None and group.deleted_at + RETENTION > cutoff
            ),
            key=lambda row: (row[0], row[1].id),
            reverse=True,
        )
        start = page * limit
        return {
            "language": self.user.language,
            "has_next": len(deleted) > start + limit,
            "bands": [
                {
                    "id": str(group.id),
                    "name": group.name,
                    "epoch": group.lifecycle_version,
                    "until": until.date().isoformat(),
                }
                for until, group in deleted[start : start + limit]
            ],
        }

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        if action == "delete":
            preview, name = payload.get("preview"), payload.get("name")
            require(
                isinstance(preview, str) and isinstance(name, str) and 0 < len(name.strip()) <= 200,
                "input",
            )
            assert isinstance(preview, str) and isinstance(name, str)
            return {"action": action, "preview": str(UUID(preview)), "name": name.strip()}
        require(
            action in ("preview_delete", "restore")
            and isinstance(payload.get("band"), str)
            and type(payload.get("epoch")) is int,
            "input",
        )
        return {"action": action, "band": str(UUID(payload["band"])), "epoch": payload["epoch"]}

    async def apply(
        self, session: UUID, data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        action = data["action"]
        if action == "delete":
            row = await self.store.get(Confirmation, UUID(data["preview"]), lock=True)
            receipt = json.loads(row.command)
            require(
                row.user_id == self.user.id
                and row.expires_at > now()
                and receipt.get("kind") == BAND_TRASH_KIND
                and receipt.get("session") == str(session)
                and receipt.get("status") == "saved"
                and bool(receipt.get("change")),
                "input_expired",
            )
            change = receipt.pop("change")
            require(data["name"] == change["name"], "confirmation")
            group = await self.trash.delete_group(UUID(change["band"]), change["fingerprint"])
            row.command = json.dumps(receipt)
            return {"band": str(group.id)}, None
        group_id = UUID(data["band"])
        if action == "restore":
            require(
                bool(
                    await self.store.find(
                        Membership,
                        user_id=self.user.id,
                        group_id=group_id,
                        role="admin",
                        status="active",
                    )
                ),
                "admin_only",
            )
            group = await self.trash.restore_group(group_id, data["epoch"])
            return {"band": str(group.id)}, None
        group = await self.groups.access(group_id, admin=True)
        require(group.lifecycle_version == data["epoch"], "conflict")
        return {"name": group.name}, {
            "band": str(group.id),
            "name": group.name,
            "fingerprint": await self.trash.group_fingerprint(group.id),
        }
