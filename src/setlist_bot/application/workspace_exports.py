"""Create and read owner-bound copies of the bot's approved PDF exports."""

from io import BytesIO
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from setlist_bot.application.exports import Exports, PdfRenderer
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import ExportFile, Group, Revision, User
from setlist_bot.domain.export_settings import FONTS, settings
from setlist_bot.domain.rules import require


class WorkspaceExports:
    def __init__(self, store: Store, user: User, bot_username: str = "") -> None:
        self.store, self.user = store, user
        self.lists = Setlists(store, user)
        self.exports = Exports(store, user, user.telegram_id, bot_username=bot_username)

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        require(action in ("generate", "settings"), "input")
        if action == "settings":
            config = payload.get("settings")
            require(
                isinstance(config, dict)
                and set(config) == {"font", "size", "align"}
                and isinstance(config["font"], str)
                and type(config["size"]) is int,
                "input",
            )
            assert isinstance(config, dict)
            return {"action": action, "settings": settings(config)}
        language, audience, version = (
            payload.get(key) for key in ("language", "audience", "version")
        )
        require(language in ("ru", "en") and audience in ("lights", "musicians", "all"), "input")
        require(type(version) is int and version > 0, "input")
        revision = payload.get("revision")
        require(revision is None or isinstance(revision, str), "input")
        return {
            "action": action,
            "language": language,
            "audience": audience,
            "version": version,
            "revision": str(UUID(revision)) if revision is not None else None,
        }

    async def options(self, group_id: UUID, concert_id: UUID) -> dict[str, Any]:
        await self.lists.load(group_id, concert_id)
        group = await self.store.get(Group, group_id)
        return {
            "settings": settings(self.user.export_settings),
            "fonts": FONTS,
            "language": group.language,
        }

    async def apply(
        self, group_id: UUID, concert_id: UUID, data: dict[str, Any], renderer: PdfRenderer | None
    ) -> dict[str, Any]:
        current = await self.lists.load(group_id, concert_id)
        if data["action"] == "settings":
            self.user.export_settings = settings(data["settings"])
            return {"settings": self.user.export_settings}
        if data["revision"]:
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
        export = await self.exports.create(
            group_id,
            concert_id,
            snapshot,
            version,
            approved=True,
            private=True,
            back="workspace",
            reuse=True,
        )
        await self.store.flush()
        assert renderer is not None
        await self.exports.files(
            export.id, data["audience"], data["language"], data["language"] == "en", renderer
        )
        return {
            "export": str(export.id),
            "language": data["language"],
            "audience": data["audience"],
        }

    async def download(
        self, group_id: UUID, concert_id: UUID, result: dict[str, Any]
    ) -> tuple[str, str, bytes]:
        require(result.get("status") == "saved" and bool(result.get("export")), "not_found")
        export = await self.exports.load(UUID(result["export"]))
        require(
            export.owner_id == self.user.id
            and export.group_id == group_id
            and export.concert_id == concert_id,
            "not_found",
        )
        audience, language = result["audience"], result["language"]
        targets = ("musicians", "lights") if audience == "all" else (audience,)
        files = []
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
        if len(files) == 1:
            return files[0].filename, "application/pdf", files[0].content
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
            for item in files:
                bundle.writestr(item.filename, item.content)
        name = files[0].filename.replace("-musicians-", "-all-").removesuffix(".pdf")
        return name + ".zip", "application/zip", output.getvalue()
