"""RU: Запросы каталога из Mini App. EN: Reuse Catalog through the bot's durable queue.

HTTP has no catalog write privilege. A request and its retry receipt live in existing
Confirmation/outbox records; the bot revalidates permissions before calling Catalog.
"""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid5

from setlist_bot.application.catalog import SONG_FIELDS, Catalog
from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.domain.entities import CatalogProposal, Confirmation, Outbox, Song, SongAlias
from setlist_bot.domain.rules import clock, duration, require, validate_song


def song_data(song: Song, aliases: list[SongAlias]) -> dict[str, Any]:
    return {
        "id": str(song.id),
        "version": song.version,
        **{key: getattr(song, key) for key in SONG_FIELDS},
        "duration": clock(song.duration_seconds),
        "aliases": sorted(alias.title for alias in aliases if alias.song_id == song.id),
    }


class MiniCatalog:
    def __init__(self, drafts: MiniDrafts) -> None:
        self.drafts = drafts
        self.store, self.user = drafts.store, drafts.user
        self.catalog = Catalog(self.store, self.user)

    async def read(self, session: UUID, song_id: UUID) -> dict[str, Any]:
        _, _, concert = await self.drafts.load(session)
        song = await self.catalog.song(concert.group_id, song_id)
        require(not song.archived, "not_found")
        aliases = await self.store.find(SongAlias, group_id=concert.group_id, song_id=song.id)
        return song_data(song, aliases)

    async def queue(self, session: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        parent, _, concert = await self.drafts.load(session)
        request = payload.get("request_id")
        require(isinstance(request, str) and str(UUID(request)) == request, "input")
        assert isinstance(request, str)
        identifier = uuid5(session, request)
        # RU: Один request_id всегда означает одни данные. EN: Lost responses are safe to retry.
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        previous = await self.store.find(Confirmation, id=identifier)
        if previous:
            data = json.loads(previous[0].command)
            require(data.get("fingerprint") == fingerprint, "input")
            return await self.status(session, identifier)
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
            song = await self.catalog.song(concert.group_id, song_id)
            require(not song.archived, "not_found")
            require(type(expected) is int and song.version == expected, "conflict")
        else:
            require(expected is None, "input")
        match = await self.catalog.exact_song(concert.group_id, data["title"])
        require(match is None or match.id == song_id, "alias_collision")
        aliases = await self.catalog.validate_aliases(
            concert.group_id, song_id, data["title"], fields["aliases"]
        )
        self.store.add(
            Confirmation(
                id=identifier,
                user_id=self.user.id,
                chat_id=self.user.telegram_id,
                expires_at=parent.expires_at,
                command=json.dumps(
                    {
                        "kind": "miniapp_catalog",
                        "session": str(session),
                        "group_id": str(concert.group_id),
                        "concert_id": str(concert.id),
                        "fingerprint": fingerprint,
                        "status": "pending",
                        "fields": data,
                        "aliases": aliases,
                        "song_id": str(song_id) if song_id else None,
                        "expected": expected,
                    }
                ),
            )
        )
        self.store.add(
            Outbox(
                chat_id=self.user.telegram_id,
                text="",
                group_id=concert.group_id,
                concert_id=concert.id,
                support_kind="catalog",
                actor=f"telegram:{self.user.telegram_id}",
                source="miniapp",
                payload={"request_id": str(identifier)},
            )
        )
        return {"status": "pending", "request": str(identifier)}

    async def status(self, session: UUID, identifier: UUID) -> dict[str, Any]:
        await self.drafts.load(session)
        row = await self.store.get(Confirmation, identifier)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and data.get("kind") == "miniapp_catalog"
            and data.get("session") == str(session),
            "miniapp_expired",
        )
        return {"request": str(identifier), "status": data["status"], **data.get("result", {})}

    async def execute(self, session: UUID, identifier: UUID) -> dict[str, Any]:
        """Called only by the bot worker with normal database privileges."""
        _, _, concert = await self.drafts.load(session)
        row = await self.store.get(Confirmation, identifier, lock=True)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and data.get("kind") == "miniapp_catalog"
            and data.get("session") == str(session),
            "miniapp_expired",
        )
        if data["status"] != "pending":
            return dict(data.get("result", {}))
        song_id = UUID(data["song_id"]) if data["song_id"] else None
        if song_id:
            require(not (await self.catalog.song(concert.group_id, song_id)).archived, "not_found")
        match = await self.catalog.exact_song(concert.group_id, data["fields"]["title"])
        require(match is None or match.id == song_id, "alias_collision")
        saved = await self.catalog.save(
            concert.group_id, data["fields"], song_id, data["expected"], aliases=data["aliases"]
        )
        proposed = isinstance(saved, CatalogProposal)
        result: dict[str, Any] = {"proposed": proposed}
        if not proposed:
            assert isinstance(saved, Song)
            result["song"] = song_data(
                saved, await self.store.find(SongAlias, group_id=concert.group_id, song_id=saved.id)
            )
        row.command = json.dumps({**data, "status": "saved", "result": result})
        return result
