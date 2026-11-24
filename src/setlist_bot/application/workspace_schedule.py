"""Personal availability and subscriptions handled with the bot's existing services."""

import base64
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from setlist_bot.application.calendars import Calendars
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import BusyPeriod, CalendarSubscription, User
from setlist_bot.domain.rules import AppError, require

SCHEDULE_SESSION = "miniapp_schedule"
SCHEDULE_KIND = "miniapp_schedule_action"
CALENDAR_TTL = 15 * 60


@dataclass(frozen=True)
class CalendarRuntime:
    origin: str
    link_key: str = field(repr=False)
    miniapp_key: bytes = field(repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.origin and self.link_key)


class CalendarHandoff:
    """Protect calendar URLs while they pass through durable request and result records."""

    def __init__(self, miniapp_key: bytes, user_id: UUID, receipt: UUID) -> None:
        key = hmac.digest(miniapp_key, b"setlist/miniapp/calendar-handoff/v1", "sha256")
        self.cipher = Fernet(base64.urlsafe_b64encode(key))
        self.user_id, self.receipt = user_id, receipt

    def seal(self, purpose: str, url: str) -> str:
        payload = [purpose, str(self.user_id), str(self.receipt), url]
        return self.cipher.encrypt(json.dumps(payload).encode()).decode()

    def open(self, purpose: str, capsule: str) -> str:
        try:
            fields = json.loads(self.cipher.decrypt(capsule.encode(), ttl=CALENDAR_TTL))
            require(
                len(fields) == 4
                and fields[:3] == [purpose, str(self.user_id), str(self.receipt)]
                and isinstance(fields[3], str),
                "input_expired",
            )
            return str(fields[3])
        except (InvalidToken, ValueError, TypeError):
            raise AppError("input_expired") from None


class WorkspaceSchedule:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.staffing = Staffing(store, user)

    @staticmethod
    def command(payload: dict[str, Any], handoff: CalendarHandoff) -> dict[str, Any]:
        action = payload.get("action")
        require(
            action
            in (
                "busy_list",
                "busy_add",
                "busy_remove",
                "calendar_list",
                "calendar_create",
                "calendar_reveal",
                "calendar_rotate",
                "calendar_disable",
                "calendar_restore",
            ),
            "input",
        )
        data: dict[str, Any] = {"action": action}
        if action == "busy_add":
            starts = date.fromisoformat(payload["starts_on"])
            ends = date.fromisoformat(payload.get("ends_on") or starts.isoformat())
            data.update(starts_on=starts.isoformat(), ends_on=ends.isoformat())
        elif action == "busy_remove":
            raw = payload.get("period")
            require(isinstance(raw, str), "input")
            data["period"] = str(UUID(raw))
        elif action == "calendar_create":
            selection = payload.get("scope")
            require(isinstance(selection, str), "input")
            data["scope"] = selection if selection in ("personal", "all") else str(UUID(selection))
            version = payload.get("version")
            require(version is None or type(version) is int, "input")
            data["version"] = version
        elif action in (
            "calendar_reveal",
            "calendar_rotate",
            "calendar_disable",
            "calendar_restore",
        ):
            raw = payload.get("subscription")
            version = payload.get("version")
            require(isinstance(raw, str) and type(version) is int, "input")
            data.update(subscription=str(UUID(raw)), version=version)
            if action == "calendar_restore":
                url = payload.get("url")
                require(isinstance(url, str) and 0 < len(url.strip()) <= 512, "calendar_wrong_link")
                assert isinstance(url, str)
                data["capsule"] = handoff.seal("restore", url.strip())
        return data

    async def calendar_listing(self, service: Calendars) -> list[dict[str, Any]]:
        groups = {str(row.id): row.name for row in await service.groups()}
        subscriptions = await self.store.find(CalendarSubscription, owner_id=self.user.id)
        rows = {
            "personal"
            if row.scope == "personal"
            else str(row.group_id)
            if row.group_id
            else "all": row
            for row in subscriptions
        }
        unavailable = sorted(
            key for key, row in rows.items() if row.enabled and row.group_id and key not in groups
        )
        result = []
        for scope in ("personal", "all", *groups, *unavailable):
            row = rows.get(scope)
            result.append(
                {
                    "scope": scope,
                    "name": groups.get(scope, ""),
                    "available": scope not in unavailable,
                    "enabled": bool(row and row.enabled),
                    "id": str(row.id) if row else None,
                    "version": row.version if row else None,
                }
            )
        return result

    async def apply(
        self, data: dict[str, Any], receipt: UUID, runtime: CalendarRuntime | None
    ) -> dict[str, Any]:
        action = data["action"]
        if action == "busy_list":
            rows = await self.store.find(BusyPeriod, user_id=self.user.id)
            rows.sort(key=lambda row: (row.starts_on, row.ends_on, row.id))
            return {
                "periods": [
                    {
                        "id": str(row.id),
                        "starts_on": row.starts_on.isoformat(),
                        "ends_on": row.ends_on.isoformat(),
                    }
                    for row in rows
                ]
            }
        if action == "busy_add":
            await self.staffing.busy(
                date.fromisoformat(data["starts_on"]), date.fromisoformat(data["ends_on"])
            )
            return {}
        if action == "busy_remove":
            await self.staffing.unbusy(UUID(data["period"]))
            return {}

        if action == "calendar_list" and (runtime is None or not runtime.enabled):
            return {"enabled": False, "scopes": []}
        require(runtime is not None and runtime.enabled, "calendar_not_ready")
        assert runtime is not None
        service = Calendars(self.store, self.user, runtime.link_key)
        if action == "calendar_list":
            return {"enabled": True, "scopes": await self.calendar_listing(service)}
        handoff = CalendarHandoff(runtime.miniapp_key, self.user.id, receipt)

        def reveal_token(token: str | None) -> dict[str, Any]:
            if token is None:
                return {"legacy": True}
            url = f"{runtime.origin}/calendar/{token}.ics"
            return {"calendar_capsule": handoff.seal("reveal", url)}

        if action == "calendar_create":
            selection = data["scope"]
            group_id = None if selection in ("all", "personal") else UUID(selection)
            scope = "personal" if selection == "personal" else "groups"
            if group_id is not None:
                await Groups(self.store, self.user).access(group_id)
            await self.store.get(User, self.user.id, lock=True)
            old = await service.subscription(group_id, scope)
            if old is not None:
                old = await self.store.get(CalendarSubscription, old.id, lock=True)
            require((old.version if old else None) == data["version"], "input_expired")
            row, token = await service.create(group_id, scope)
            return reveal_token(token or await service.reveal(row.id))
        identifier = UUID(data["subscription"])
        version = data["version"]
        if action in ("calendar_rotate", "calendar_disable"):
            _, token = await service.change(identifier, version, action.removeprefix("calendar_"))
            return reveal_token(token) if action == "calendar_rotate" else {}

        if action == "calendar_restore":
            url = handoff.open("restore", data["capsule"])
            match = re.fullmatch(
                re.escape(runtime.origin) + r"/calendar/([A-Za-z0-9_-]{43})\.ics", url
            )
            require(match is not None, "calendar_wrong_link")
            assert match is not None
            await service.restore(identifier, version, match[1])
            return reveal_token(match[1])
        row = await service.owned(identifier)
        row = await self.store.get(CalendarSubscription, row.id, lock=True)
        require(row.enabled and row.version == version, "input_expired")
        try:
            token = await service.reveal(identifier)
        except AppError as exc:
            if exc.code != "calendar_link_unreadable":
                raise
            token = None
        return reveal_token(token)
