import asyncio
import re
from copy import deepcopy
from datetime import timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from unidecode import unidecode

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.concerts import date_label
from setlist_bot.domain.entities import ChatBinding, Concert, ExportFile, ExportSnapshot, User, now
from setlist_bot.domain.export_settings import settings
from setlist_bot.domain.rules import require

Audience = Literal["musicians", "lights"]


class PdfRenderer(Protocol):
    def render(
        self, payload: dict[str, Any], audience: Audience, language: str, transliterate: bool
    ) -> bytes: ...


class Exports:
    def __init__(self, store: Store, user: User, chat_id: int, *, bot_username: str = "") -> None:
        self.store, self.user, self.chat_id = store, user, chat_id
        self.bot_username = bot_username

    async def create(
        self,
        group_id: UUID,
        concert_id: UUID,
        snapshot: dict[str, Any],
        version: int,
        *,
        approved: bool,
        private: bool,
        back: str,
    ) -> ExportSnapshot:
        require(approved, "export_approved_only")
        group = await Groups(self.store, self.user).access(group_id)
        await Setlists(self.store, self.user).load(group_id, concert_id)
        concert = await self.store.get(Concert, concert_id)
        require(any(block["items"] for block in snapshot["blocks"]), "export_empty")
        export = ExportSnapshot(
            group_id=group_id,
            concert_id=concert_id,
            chat_id=self.chat_id,
            owner_id=self.user.id if private else None,
            group_epoch=group.lifecycle_version,
            concert_epoch=concert.lifecycle_version,
            expires_at=now() + timedelta(days=30),
            payload={
                "group_name": group.name,
                "concert_date": concert.date.isoformat(),
                "concert_label": date_label(concert.date, group.language),
                "city": concert.city,
                "extra_info": concert.extra_info,
                "performance_duration_seconds": concert.performance_duration_seconds,
                "settings": settings(self.user.export_settings),
                "layout_version": 2,
                "bot_username": self.bot_username,
                "concert_title": concert.title,
                "version": version,
                "approved": approved,
                "snapshot": deepcopy(snapshot),
                "back": back,
                "language": group.language,
            },
        )
        self.store.add(export)
        return export

    async def configured(self, source: ExportSnapshot) -> ExportSnapshot:
        """A new immutable cache key for the requesting user's current typography."""
        config = settings(self.user.export_settings)
        if (
            source.payload.get("settings") == config
            and source.payload.get("layout_version") == 2
            and source.payload.get("bot_username") == self.bot_username
        ):
            return source
        result = ExportSnapshot(
            group_id=source.group_id,
            concert_id=source.concert_id,
            chat_id=source.chat_id,
            owner_id=source.owner_id,
            group_epoch=source.group_epoch,
            concert_epoch=source.concert_epoch,
            expires_at=source.expires_at,
            payload={
                **deepcopy(source.payload),
                "settings": config,
                "layout_version": 2,
                "bot_username": self.bot_username,
            },
        )
        self.store.add(result)
        return result

    async def load(self, export_id: UUID) -> ExportSnapshot:
        export = await self.store.get(ExportSnapshot, export_id)
        require(export.chat_id == self.chat_id, "not_found")
        require(export.owner_id is None or export.owner_id == self.user.id, "not_found")
        require(export.expires_at > now(), "export_expired")
        require(export.payload.get("approved") is True, "export_approved_only")
        group = await Groups(self.store, self.user).access(export.group_id)
        await Setlists(self.store, self.user).load(export.group_id, export.concert_id)
        concert = await self.store.get(Concert, export.concert_id)
        require(
            export.group_epoch == group.lifecycle_version
            and export.concert_epoch == concert.lifecycle_version,
            "export_expired",
        )
        if export.owner_id is None:
            bindings = await self.store.find(ChatBinding, chat_id=self.chat_id)
            require(bool(bindings) and bindings[0].group_id == export.group_id, "not_found")
        return export

    async def files(
        self,
        export_id: UUID,
        audience: str,
        language: str,
        transliterate: bool,
        renderer: PdfRenderer,
    ) -> list[tuple[str, bytes]]:
        require(audience in ("musicians", "lights", "all") and language in ("ru", "en"), "input")
        export = await self.load(export_id)
        result = []
        targets: tuple[Audience, ...] = (
            ("musicians", "lights")
            if audience == "all"
            else ("musicians" if audience == "musicians" else "lights",)
        )
        for target in targets:
            existing = await self.store.find(
                ExportFile,
                export_id=export.id,
                audience=target,
                language=language,
                transliterate=transliterate,
            )
            if existing:
                result.append((existing[0].filename, existing[0].content))
                continue
            content = await asyncio.to_thread(
                renderer.render, export.payload, target, language, transliterate
            )
            group_name = (
                re.sub(r"[^a-z0-9]+", "-", unidecode(export.payload["group_name"]).lower()).strip(
                    "-"
                )[:40]
                or "setlist"
            )
            name = (
                f"{group_name}-{export.payload['concert_date']}-v{export.payload['version']}-"
                f"{target}-{language}{'-latin' if transliterate else ''}"
                f"{'-draft' if not export.payload['approved'] else ''}.pdf"
            )
            self.store.add(
                ExportFile(
                    export_id=export.id,
                    audience=target,
                    language=language,
                    transliterate=transliterate,
                    filename=name,
                    content=content,
                )
            )
            result.append((name, content))
        return result
