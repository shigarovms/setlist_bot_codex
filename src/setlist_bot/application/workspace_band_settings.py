"""Collective settings shared by the group chat and the workspace."""

from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Membership, User
from setlist_bot.domain.rules import require

BAND_SETTINGS_KIND = "miniapp_band_settings"
SETLIST_FIELDS = ("setlist_style", "admins_only_suggestions")
SETTING_FIELDS = (*SETLIST_FIELDS, "default_pause_seconds", "language", "reminders_enabled")


def validate(values: dict[str, Any]) -> None:
    for field, value in values.items():
        if field == "setlist_style":
            require(value in ("mono", "article"), "input")
        elif field == "language":
            require(value in ("ru", "en"), "input")
        elif field == "default_pause_seconds":
            require(type(value) is int and 0 <= value <= 86400, "pause_input")
        else:
            require(type(value) is bool, "input")


class WorkspaceBandSettings:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)

    async def view(self, group_id: UUID) -> dict[str, Any]:
        group = await self.groups.access(group_id)
        member = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
        admin = member.role == "admin"
        return {
            "band": group.name,
            "language": self.user.language,
            "admin": admin,
            "settings": {
                key: getattr(group, key) for key in SETTING_FIELDS if admin or key in SETLIST_FIELDS
            },
        }

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        changes, expected = payload.get("changes"), payload.get("expected")
        require(
            isinstance(changes, dict)
            and bool(changes)
            and set(changes) <= set(SETTING_FIELDS)
            and isinstance(expected, dict)
            and expected.keys() == changes.keys(),
            "input",
        )
        assert isinstance(changes, dict) and isinstance(expected, dict)
        validate(changes)
        validate(expected)
        return {"changes": changes, "expected": expected}

    async def apply(self, group_id: UUID, data: dict[str, Any]) -> None:
        group = await self.groups.access(group_id, admin=True)
        require(
            all(getattr(group, key) == value for key, value in data["expected"].items()),
            "conflict",
        )
        changes = {
            key: value for key, value in data["changes"].items() if getattr(group, key) != value
        }
        appearance_changed = any(key in changes for key in SETLIST_FIELDS)
        if appearance_changed:
            await self.groups.setlist_settings(
                group_id,
                style=changes.get("setlist_style"),
                admins_only=changes.get("admins_only_suggestions"),
            )
        if "language" in changes:
            await self.groups.set_language(group_id, changes["language"])
        if "default_pause_seconds" in changes:
            seconds = changes["default_pause_seconds"]
            await self.groups.set_default_pause(group_id, str(seconds))
            record_activity(
                self.store, self.user, "group_default_pause_changed", group_id, seconds=seconds
            )
        if "reminders_enabled" in changes:
            await self.groups.set_reminders(group_id, changes["reminders_enabled"])
        elif appearance_changed:
            await queue_card_refreshes(self.store, group_id)
