"""Create and read owner-bound copies of the bot's approved PDF exports."""

import asyncio
from typing import Any
from uuid import UUID

from setlist_bot.application.exports import Exports, PdfPreviewer, PdfRenderer, export_audiences
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.workspace_gigs import WorkspaceGigs
from setlist_bot.domain.entities import ExportFile, Revision, User
from setlist_bot.domain.export_settings import FONTS, MAX_TARGET_PAGES, settings, validated_settings
from setlist_bot.domain.rules import require


class WorkspaceExports:
    def __init__(
        self,
        store: Store,
        user: User,
        bot_username: str = "",
        *,
        invitation_id: UUID | None = None,
    ) -> None:
        self.store, self.user = store, user
        self.lists = Setlists(store, user)
        self.gigs = WorkspaceGigs(store, user)
        self.invitation_id = invitation_id
        self.exports = Exports(
            store, user, user.telegram_id, bot_username=bot_username, invitation_id=invitation_id
        )

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        require(action in ("generate", "settings"), "input")
        if action == "settings":
            return {"action": action, "settings": validated_settings(payload.get("settings"))}
        language, audience, version = (
            payload.get(key) for key in ("language", "audience", "version")
        )
        require(language in ("ru", "en") and audience in ("lights", "musicians", "all"), "input")
        delivery = payload.get("delivery", "download")
        require(delivery in ("download", "telegram"), "input")
        require(type(version) is int and version > 0, "input")
        revision = payload.get("revision")
        require(revision is None or isinstance(revision, str), "input")
        return {
            "action": action,
            "language": language,
            "audience": audience,
            "delivery": delivery,
            "version": version,
            "revision": str(UUID(revision)) if revision is not None else None,
            **(
                {"settings": validated_settings(payload["settings"])}
                if "settings" in payload
                else {}
            ),
        }

    async def options(self, group_id: UUID, concert_id: UUID) -> dict[str, Any]:
        group, _ = await self.exports.event(group_id, concert_id)
        return {
            "settings": settings(self.user.export_settings),
            "fonts": FONTS,
            "language": group.language,
            "max_pages": MAX_TARGET_PAGES,
        }

    async def _approved(
        self, group_id: UUID, concert_id: UUID, data: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        current = None
        guest = None
        if self.invitation_id is not None:
            guest = await self.gigs.confirmed(self.invitation_id)
            require(
                guest.group.id == group_id
                and guest.event.id == concert_id
                and not data.get("revision"),
                "forbidden",
            )
        else:
            current = await self.lists.load(group_id, concert_id)
        if guest:
            snapshot, version, approved = await self.gigs.shared(guest)
            require(approved, "export_approved_only")
        elif data["revision"]:
            assert current is not None
            revision = await self.store.get(Revision, UUID(data["revision"]))
            require(
                revision.group_id == group_id and revision.setlist_id == current.id, "not_found"
            )
            require(revision.action == "approved", "export_approved_only")
            snapshot, version = revision.snapshot, revision.number
        else:
            snapshot, version, approved = await self.lists.read(group_id, concert_id)
            require(approved, "export_approved_only")
        require(version == data["version"], "conflict")
        require(any(block["items"] for block in snapshot["blocks"]), "export_empty")
        return snapshot, version

    async def preview(
        self, group_id: UUID, concert_id: UUID, data: dict[str, Any], renderer: PdfPreviewer
    ) -> dict[str, Any]:
        snapshot, _ = await self._approved(group_id, concert_id, data)
        audiences = export_audiences(data["audience"])
        payload = {
            "snapshot": snapshot,
            "settings": data.get("settings", self.user.export_settings),
        }
        if "lights" in audiences:
            payload["lighting_snapshot"] = await self.exports.lighting_snapshot(group_id, snapshot)
        return await asyncio.to_thread(renderer.preview, payload, audiences, data["language"])

    async def apply(
        self, group_id: UUID, concert_id: UUID, data: dict[str, Any], renderer: PdfRenderer | None
    ) -> dict[str, Any]:
        if data["action"] == "settings":
            await self.exports.event(group_id, concert_id)
            self.user.export_settings = settings(data["settings"])
            return {"settings": self.user.export_settings}
        snapshot, version = await self._approved(group_id, concert_id, data)
        export = await self.exports.create(
            group_id,
            concert_id,
            snapshot,
            version,
            approved=True,
            private=True,
            back="workspace",
            reuse=True,
            appearance=data.get("settings"),
        )
        await self.store.flush()
        assert renderer is not None
        await self.exports.files(
            export.id,
            data["audience"],
            data["language"],
            data["language"] == "en",
            renderer,
            refresh_appearance=False,
        )
        return {
            "export": str(export.id),
            "language": data["language"],
            "audience": data["audience"],
            "delivery": data.get("delivery", "download"),
        }

    async def downloads(
        self, group_id: UUID, concert_id: UUID, result: dict[str, Any], audience: str | None
    ) -> list[ExportFile]:
        require(result.get("status") == "saved" and bool(result.get("export")), "not_found")
        export = await self.exports.load(UUID(result["export"]))
        require(
            export.owner_id == self.user.id
            and export.group_id == group_id
            and export.concert_id == concert_id,
            "not_found",
        )
        if self.invitation_id is not None:
            guest = await self.gigs.confirmed(self.invitation_id)
            _, version, approved = await self.gigs.shared(guest)
            require(approved and version == export.payload["version"], "export_expired")
        language = result["language"]
        targets = export_audiences(result["audience"])
        if audience is not None:
            require(audience in targets, "not_found")
            targets = export_audiences(audience)
        files: list[ExportFile] = []
        for target in targets:
            matches = await self.store.find(
                ExportFile,
                export_id=export.id,
                audience=target,
                language=language,
                transliterate=language == "en",
            )
            require(bool(matches), "not_found")
            files.append(matches[0])
        return files
