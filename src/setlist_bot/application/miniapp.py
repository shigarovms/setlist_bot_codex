"""Private HTML input adapter. Save through Setlists.edit_songs, never publish/approve."""

import hashlib
import json
from collections import Counter
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.concerts import button_date, today
from setlist_bot.domain.entities import Concert, Confirmation, Revision, Setlist, Song, User, now
from setlist_bot.domain.rules import flat_content, is_pause, normalize, parse_pause, require, totals


def entries(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for block in flat_content(snapshot)["blocks"]:
        for item in block["items"]:
            if is_pause(item):
                result.append({"kind": "pause", "duration_seconds": item["duration_seconds"]})
            else:
                result.append(
                    {
                        "kind": "song",
                        "song_id": item["song_id"],
                        "version": item["song_version"],
                        "title": item["title"],
                        "duration_seconds": item["duration_seconds"],
                        "pause_before": item.get("pause_before"),
                    }
                )
    return result


class MiniDrafts:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups, self.lists = Groups(store, user), Setlists(store, user)

    async def create(self, group_id: UUID, concert_id: UUID) -> Confirmation:
        await self.groups.can_suggest(group_id)
        group = await self.groups.access(group_id)
        concert = await self.store.get(Concert, concert_id)
        await self.lists.load(group_id, concert_id)
        session = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=24),
            command=json.dumps(
                {
                    "kind": "miniapp",
                    "group_id": str(group_id),
                    "concert_id": str(concert_id),
                    "group_epoch": group.lifecycle_version,
                    "concert_epoch": concert.lifecycle_version,
                }
            ),
        )
        self.store.add(session)
        return session

    async def load(self, identifier: UUID) -> tuple[Confirmation, dict[str, Any], Concert]:
        row = await self.store.get(Confirmation, identifier)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and row.chat_id == self.user.telegram_id
            and row.expires_at > now()
            and data.get("kind") == "miniapp",
            "miniapp_expired",
        )
        group_id = UUID(data["group_id"])
        await self.groups.can_suggest(group_id)
        group = await self.groups.access(group_id)
        concert = await self.store.get(Concert, UUID(data["concert_id"]))
        require(
            concert.group_id == group_id
            and concert.deleted_at is None
            and group.lifecycle_version == data["group_epoch"]
            and concert.lifecycle_version == data["concert_epoch"],
            "miniapp_expired",
        )
        # RU: Сначала блокируем коллектив, затем сессию, как в командах бота.
        # EN: Match the bot's lock order; the session target cannot switch with /groups.
        row = await self.store.get(Confirmation, identifier, lock=True)
        return row, json.loads(row.command), concert

    async def state(self, identifier: UUID) -> dict[str, Any]:
        _, data, concert = await self.load(identifier)
        group_id = UUID(data["group_id"])
        group = await self.groups.access(group_id)
        snapshot, version, _ = await self.lists.read(group_id, concert.id, private=True)
        songs = await self.store.find(Song, group_id=group_id, archived=False)
        require(len(songs) <= 5000, "miniapp_catalog_limit")
        counts: Counter[str] = Counter()
        active = {c.id for c in await self.store.find(Concert, group_id=group_id, deleted_at=None)}
        revisions = {
            s.approved_revision_id
            for s in await self.store.find(Setlist, group_id=group_id)
            if s.approved_revision_id and s.concert_id in active
        }
        for revision in await self.store.get_many(Revision, revisions):
            if revision.group_id == group_id:
                counts.update(
                    item["song_id"]
                    for b in flat_content(revision.snapshot)["blocks"]
                    for item in b["items"]
                    if not is_pause(item)
                )
        songs.sort(key=lambda song: (-counts[str(song.id)], normalize(song.title), song.id))
        return {
            "language": self.user.language,
            "group": group.name,
            "title": concert.title,
            "date": button_date(concert.date),
            "concert_id": str(concert.id),
            "version": version,
            "default_pause_seconds": snapshot["default_pause_seconds"],
            "planned_seconds": concert.performance_duration_seconds,
            "entries": entries(snapshot),
            "catalog": [
                {
                    "id": str(song.id),
                    "title": song.title,
                    "version": song.version,
                    "duration_seconds": song.duration_seconds,
                }
                for song in songs
            ],
        }

    async def history(self, identifier: UUID, index: int) -> dict[str, Any]:
        _, data, target = await self.load(identifier)
        group_id = UUID(data["group_id"])
        concerts = {
            c.id: c
            for c in await self.store.find(Concert, group_id=group_id, deleted_at=None)
            if c.date < min(today(), target.date)
        }
        sources = [
            s
            for s in await self.store.find(Setlist, group_id=group_id)
            if s.concert_id in concerts and s.approved_revision_id
        ]
        sources.sort(
            key=lambda source: (concerts[source.concert_id].date, source.concert_id), reverse=True
        )
        require(type(index) is int and 0 <= index < len(sources), "miniapp_no_history")
        source = sources[index]
        assert source.approved_revision_id is not None
        revision = await self.store.get(Revision, source.approved_revision_id)
        require(revision.group_id == group_id and revision.setlist_id == source.id, "forbidden")
        concert = concerts[source.concert_id]
        return {
            "index": index,
            "count": len(sources),
            "label": " · ".join(
                part for part in (button_date(concert.date), concert.title) if part
            ),
            "seconds": totals(revision.snapshot)[2],
            "entries": entries(revision.snapshot),
        }

    async def save(
        self, identifier: UUID, expected: int, values: list[dict[str, Any]], request_id: str
    ) -> dict[str, Any]:
        row, data, concert = await self.load(identifier)
        require(type(expected) is int and isinstance(values, list) and len(values) <= 200, "input")
        require(all(isinstance(item, dict) for item in values), "input")
        require(isinstance(request_id, str), "input")
        require(str(UUID(request_id)) == request_id, "input")
        require(
            all(
                item.get("kind") != "song"
                or isinstance(item.get("song_id"), str)
                and type(item.get("version")) is int
                for item in values
            ),
            "input",
        )
        fingerprint = hashlib.sha256(
            json.dumps([expected, values], sort_keys=True).encode()
        ).hexdigest()
        if data.get("save_request") == request_id:
            require(data.get("save_fingerprint") == fingerprint, "input")
            current = await self.state(identifier)
            require(current["version"] == data["saved_version"], "conflict")
            return current
        group_id = UUID(data["group_id"])
        ordered: list[UUID | int] = []
        before: list[int | None] = []
        ids = {UUID(item["song_id"]) for item in values if item.get("kind") == "song"}
        songs = {song.id: song for song in await self.store.get_many(Song, ids)}
        for item in values:
            if item.get("kind") == "pause":
                seconds = item["duration_seconds"]
                require(type(seconds) is int and 0 < seconds <= 86400, "pause_duration")
                ordered.append(seconds)
                before.append(None)
            else:
                require(item.get("kind") == "song", "input")
                song = songs.get(UUID(item["song_id"]))
                require(
                    song is not None
                    and song.group_id == group_id
                    and not song.archived
                    and song.version == item.get("version"),
                    "selection_catalog_changed",
                )
                assert song is not None
                lead = item.get("pause_before")
                require(lead is None or type(lead) is int and 0 <= lead <= 86400, "pause_duration")
                ordered.append(song.id)
                before.append(lead)
        await self.lists.edit_songs(
            group_id,
            concert.id,
            expected,
            [],
            replace=True,
            private=True,
            ordered=ordered,
            pauses_before=before,
        )
        result = await self.state(identifier)
        row.command = json.dumps(
            {
                **data,
                "save_request": request_id,
                "save_fingerprint": fingerprint,
                "saved_version": result["version"],
            }
        )
        record_activity(
            self.store, self.user, "miniapp_draft_saved", concert.id, group_id=str(group_id)
        )
        return result

    async def pause(self, identifier: UUID, value: str) -> int:
        await self.load(identifier)
        require(isinstance(value, str) and len(value) <= 80, "pause_duration")
        text = value.strip()
        seconds = parse_pause(text + " мин" if text.isdecimal() else text)
        require(seconds is not None and 0 < seconds <= 86400, "pause_duration")
        assert seconds is not None
        return seconds
