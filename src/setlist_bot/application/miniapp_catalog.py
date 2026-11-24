"""RU: Запросы каталога из Mini App. EN: Reuse Catalog through the bot's durable queue.

HTTP has no catalog write privilege. A request and its retry receipt live in existing
Confirmation/outbox records; the bot revalidates permissions before calling Catalog.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

from setlist_bot.application.catalog import SONG_FIELDS, Catalog, song_data
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.importing import Importer
from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.repertoire_import import RepertoireImport
from setlist_bot.application.workspace_music import WorkspaceMusic
from setlist_bot.application.workspace_setlists import SETLIST_SESSION, WorkspaceSetlists
from setlist_bot.domain.entities import (
    CatalogProposal,
    Concert,
    Confirmation,
    Outbox,
    Song,
    SongAlias,
    now,
)
from setlist_bot.domain.performance_input import performance_seconds
from setlist_bot.domain.rules import duration, require, validate_song

CATALOG_OPERATIONS = {
    "catalog_save": "miniapp_catalog",
    "catalog_archive": "miniapp_catalog_archive",
    "catalog_restore": "miniapp_catalog_restore",
    "catalog_decide": "miniapp_catalog_decide",
    "catalog_import": "miniapp_catalog_import",
    "music_action": "miniapp_music",
    "shared_action": "miniapp_shared",
    "duration_save": "miniapp_duration",
}


@dataclass(frozen=True)
class CatalogTarget:
    parent: Confirmation
    group_id: UUID
    concert: Concert | None = None
    shared: bool = False


class MiniCatalog:
    def __init__(self, drafts: MiniDrafts) -> None:
        self.drafts = drafts
        self.store, self.user = drafts.store, drafts.user
        self.catalog = Catalog(self.store, self.user)

    async def open_repertoire(self, group_id: UUID) -> Confirmation:
        group = await self.drafts.groups.access(group_id)
        parent = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=24),
            command=json.dumps(
                {
                    "kind": "miniapp_repertoire",
                    "group_id": str(group_id),
                    "group_epoch": group.lifecycle_version,
                }
            ),
        )
        self.store.add(parent)
        return parent

    async def target(self, session: UUID) -> CatalogTarget:
        parent = await self.store.get(Confirmation, session)
        data = json.loads(parent.command)
        kind = data.get("kind")
        if kind not in ("miniapp_repertoire", SETLIST_SESSION):
            parent, _, concert = await self.drafts.load(session)
            return CatalogTarget(parent, concert.group_id, concert)
        require(
            parent.user_id == self.user.id
            and parent.chat_id == self.user.telegram_id
            and parent.expires_at > now(),
            "miniapp_expired",
        )
        group = await self.drafts.groups.access(UUID(data["group_id"]))
        require(group.lifecycle_version == data["group_epoch"], "miniapp_expired")
        if kind == SETLIST_SESSION:
            concert = await self.store.get(Concert, UUID(data["concert_id"]))
            require(
                concert.group_id == group.id
                and concert.deleted_at is None
                and concert.lifecycle_version == data["concert_epoch"],
                "miniapp_expired",
            )
            await self.drafts.groups.check_performance_visibility(group.id, concert.id)
            return CatalogTarget(parent, group.id, concert, shared=True)
        return CatalogTarget(parent, group.id)

    async def read(self, session: UUID, song_id: UUID) -> dict[str, Any]:
        target = await self.target(session)
        song = await self.catalog.song(target.group_id, song_id)
        require(target.concert is None or not song.archived, "not_found")
        aliases = await self.store.find(SongAlias, group_id=target.group_id, song_id=song.id)
        return {**song_data(song, aliases), "archived": song.archived}

    async def queue(
        self,
        session: UUID,
        payload: dict[str, Any],
        *,
        operation: str = "catalog_save",
    ) -> dict[str, Any]:
        target = await self.target(session)
        group_id, concert = target.group_id, target.concert
        kind = CATALOG_OPERATIONS[operation]
        require(target.shared == (operation == "shared_action"), "input")
        if operation not in ("catalog_save", "shared_action"):
            await self.drafts.groups.access(group_id, admin=True)
        request = payload.get("request_id")
        require(isinstance(request, str) and str(UUID(request)) == request, "input")
        assert isinstance(request, str)
        identifier = uuid5(session, request)
        # RU: Один request_id всегда означает одни данные. EN: Lost responses are safe to retry.
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        previous = await self.store.find(Confirmation, id=identifier)
        if previous:
            data = json.loads(previous[0].command)
            require(data.get("kind") == kind and data.get("fingerprint") == fingerprint, "input")
            return await self.status(session, identifier)
        expires = target.parent.expires_at
        if operation == "duration_save":
            require(concert is not None, "not_found")
            assert concert is not None
            expected = payload.get("expected")
            require(type(expected) is int and concert.metadata_version == expected, "conflict")
            value = payload.get("value")
            require(isinstance(value, str) and len(value) <= 80, "pause_duration")
            assert isinstance(value, str)
            require(value == "-" or performance_seconds(value) is not None, "pause_duration")
            details = {"value": value, "expected": expected}
        elif operation in ("catalog_archive", "catalog_restore"):
            song_id = payload.get("song_id")
            require(isinstance(song_id, str), "input")
            assert isinstance(song_id, str)
            song = await self.catalog.song(group_id, UUID(song_id))
            expected = payload.get("expected")
            require(type(expected) is int and song.version == expected, "conflict")
            require(song.archived == (operation == "catalog_restore"), "conflict")
            details = {"song_id": str(song.id), "expected": expected}
        elif operation == "catalog_decide":
            proposal = await self.store.get(CatalogProposal, UUID(payload["proposal_id"]))
            require(proposal.group_id == group_id, "not_found")
            require(proposal.status == "pending", "already_done")
            require(type(payload.get("approve")) is bool, "input")
            details = {"proposal_id": str(proposal.id), "approve": payload["approve"]}
        elif operation == "catalog_import":
            source = RepertoireImport.from_payload(payload)
            importer = Importer(self.store, self.user, self.user.telegram_id)
            plan = await source.preview(importer, group_id)
            Importer.check_plan(plan, payload["fingerprint"])
            details = {**source.data(), "import_fingerprint": plan.fingerprint}
            expires = min(expires, now() + timedelta(minutes=30))
        elif operation == "music_action":
            require(concert is None, "input")
            details = {"music": WorkspaceMusic.command(payload)}
        elif operation == "shared_action":
            details = {"shared": WorkspaceSetlists.command(payload)}
        else:
            details = await self._catalog_details(group_id, payload)
        self.store.add(
            Confirmation(
                id=identifier,
                user_id=self.user.id,
                chat_id=self.user.telegram_id,
                expires_at=expires,
                command=json.dumps(
                    {
                        "kind": kind,
                        "session": str(session),
                        "group_id": str(group_id),
                        "concert_id": str(concert.id) if concert else None,
                        "fingerprint": fingerprint,
                        "status": "pending",
                        **details,
                    }
                ),
            )
        )
        self.store.add(
            Outbox(
                chat_id=self.user.telegram_id,
                text="",
                group_id=group_id,
                concert_id=concert.id if concert else None,
                support_kind="catalog",
                actor=f"telegram:{self.user.telegram_id}",
                source="miniapp",
                payload={"request_id": str(identifier)},
            )
        )
        return {"status": "pending", "request": str(identifier)}

    async def _catalog_details(self, group_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        song_id = UUID(payload["song_id"]) if payload.get("song_id") else None
        fields = payload.get("fields")
        require(isinstance(fields, dict) and set(fields) == {*SONG_FIELDS, "aliases"}, "song_input")
        assert isinstance(fields, dict)
        data = {key: fields[key] for key in SONG_FIELDS}
        # UI sends MM:SS; never interpret a song duration as an event duration.
        data["duration_seconds"] = duration(fields["duration_seconds"])
        require(isinstance(data["title"], str), "song_input")
        data["title"] = data["title"].strip()
        validate_song(data)
        require(type(data["bpm"]) is int or data["bpm"] is None, "song_input")
        expected = payload.get("expected")
        if song_id:
            song = await self.catalog.song(group_id, song_id)
            require(not song.archived, "not_found")
            require(type(expected) is int and song.version == expected, "conflict")
        else:
            require(expected is None, "input")
        match = await self.catalog.exact_song(group_id, data["title"])
        require(match is None or match.id == song_id, "alias_collision")
        aliases = await self.catalog.validate_aliases(
            group_id, song_id, data["title"], fields["aliases"]
        )
        return {
            "fields": data,
            "aliases": aliases,
            "song_id": str(song_id) if song_id else None,
            "expected": expected,
        }

    async def status(self, session: UUID, identifier: UUID) -> dict[str, Any]:
        await self.target(session)
        row = await self.store.get(Confirmation, identifier)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and data.get("kind") in CATALOG_OPERATIONS.values()
            and data.get("session") == str(session),
            "miniapp_expired",
        )
        return {"request": str(identifier), "status": data["status"], **data.get("result", {})}

    async def execute(self, target: CatalogTarget, identifier: UUID) -> dict[str, Any]:
        """Called only by the bot worker with normal database privileges."""
        group_id, concert = target.group_id, target.concert
        row = await self.store.get(Confirmation, identifier, lock=True)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and data.get("kind") in CATALOG_OPERATIONS.values()
            and data.get("session") == str(target.parent.id)
            and data.get("group_id") == str(group_id)
            and data.get("concert_id") == (str(concert.id) if concert else None)
            and row.expires_at > now(),
            "miniapp_expired",
        )
        if data["status"] != "pending":
            return dict(data.get("result", {}))
        if data["kind"] == "miniapp_duration":
            require(concert is not None, "not_found")
            assert concert is not None
            await self.drafts.groups.access(group_id, admin=True)
            require(concert.metadata_version == data["expected"], "conflict")
            saved_event = await ConcertDetails(self.store, self.user).update(
                group_id, concert.id, "concert_duration", data["value"]
            )
            duration_result = {
                "planned_seconds": saved_event.performance_duration_seconds,
                "metadata_version": saved_event.metadata_version,
            }
            row.command = json.dumps({**data, "status": "saved", "result": duration_result})
            return duration_result
        if data["kind"] in ("miniapp_catalog_archive", "miniapp_catalog_restore"):
            archived = data["kind"] == "miniapp_catalog_archive"
            await self.catalog.archive(
                group_id, UUID(data["song_id"]), data["expected"], archived=archived
            )
            archive_result = {"archived": archived}
            row.command = json.dumps({**data, "status": "saved", "result": archive_result})
            return archive_result
        if data["kind"] == "miniapp_catalog_decide":
            await self.catalog.decide(group_id, UUID(data["proposal_id"]), data["approve"])
            decision = {"approved": data["approve"]}
            row.command = json.dumps({**data, "status": "saved", "result": decision})
            return decision
        if data["kind"] == "miniapp_catalog_import":
            importer = Importer(self.store, self.user, self.user.telegram_id)
            imported = await importer.commit_source(
                group_id,
                data["source"],
                data["mapping"],
                data["has_header"],
                data["import_fingerprint"],
            )
            summary = {"added": imported.added, "supplemented": imported.supplemented}
            # The source is no longer needed after import. Keep the idempotency receipt.
            receipt = {key: value for key, value in data.items() if key != "source"}
            row.command = json.dumps({**receipt, "status": "saved", "result": summary})
            return summary
        if data["kind"] == "miniapp_music":
            music_result = await WorkspaceMusic(self.store, self.user).apply(
                group_id, data["music"]
            )
            row.command = json.dumps({**data, "status": "saved", "result": music_result})
            return music_result
        if data["kind"] == "miniapp_shared":
            require(target.shared and concert is not None, "miniapp_expired")
            assert concert is not None
            shared_result = await WorkspaceSetlists(self.store, self.user).apply(
                group_id, concert.id, data["shared"]
            )
            public = {
                key: value
                for key, value in shared_result.items()
                if key not in ("notify", "resolved")
            }
            row.command = json.dumps({**data, "status": "saved", "result": public})
            return shared_result
        song_id = UUID(data["song_id"]) if data["song_id"] else None
        if song_id:
            require(not (await self.catalog.song(group_id, song_id)).archived, "not_found")
        match = await self.catalog.exact_song(group_id, data["fields"]["title"])
        require(match is None or match.id == song_id, "alias_collision")
        saved = await self.catalog.save(
            group_id, data["fields"], song_id, data["expected"], aliases=data["aliases"]
        )
        proposed = isinstance(saved, CatalogProposal)
        result: dict[str, Any] = {"proposed": proposed}
        if not proposed:
            assert isinstance(saved, Song)
            result["song"] = song_data(
                saved, await self.store.find(SongAlias, group_id=group_id, song_id=saved.id)
            )
        row.command = json.dumps({**data, "status": "saved", "result": result})
        return result
