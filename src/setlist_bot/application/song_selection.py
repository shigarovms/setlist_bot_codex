"""Temporary private input sessions; confirmed input uses the ordinary draft writer."""

import json
from collections import Counter
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Concert, Confirmation, Revision, Setlist, User, now
from setlist_bot.domain.rules import normalize, require

PAGE_SIZE = 40
SESSION_TTL = timedelta(hours=24)
KIND = "song_selection"


class SongSelection:
    def __init__(self, store: Store, user: User, chat_id: int) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id

    @staticmethod
    def data(session: Confirmation) -> dict[str, Any]:
        return dict(json.loads(session.command))

    @staticmethod
    def write(session: Confirmation, data: dict[str, Any]) -> None:
        session.command = json.dumps(data, ensure_ascii=False)
        session.expires_at = now() + SESSION_TTL

    async def active(self) -> Confirmation | None:
        for session in await self.store.find(
            Confirmation, user_id=self.user.id, chat_id=self.chat_id
        ):
            if self.data(session).get("kind") == KIND and session.expires_at > now():
                return session
        return None

    async def clear(self) -> bool:
        found = False
        for session in await self.store.find(
            Confirmation, user_id=self.user.id, chat_id=self.chat_id
        ):
            if self.data(session).get("kind") == KIND:
                await self.store.delete(session)
                found = True
        return found

    async def create(
        self, group_id: UUID, concert_id: UUID, version: int, template_id: UUID
    ) -> Confirmation:
        group = await Groups(self.store, self.user).access(group_id)
        concert = await self.store.get(Concert, concert_id)
        await self.clear()
        session = Confirmation(
            user_id=self.user.id, chat_id=self.chat_id, command="", expires_at=now() + SESSION_TTL
        )
        self.write(
            session,
            {
                "kind": KIND,
                "stage": "method",
                "group_id": str(group_id),
                "concert_id": str(concert_id),
                "group_epoch": group.lifecycle_version,
                "concert_epoch": concert.lifecycle_version,
                "version": version,
                "template_id": str(template_id),
                "page": 0,
                "songs": [],
                "selected": [],
                "panel_ids": [],
                "keyboard_message_id": None,
            },
        )
        self.store.add(session)
        return session

    async def load(self, identifier: UUID) -> tuple[Confirmation, dict[str, Any]]:
        matches = await self.store.find(Confirmation, id=identifier)
        require(bool(matches), "selection_expired")
        session = matches[0]
        data = self.data(session)
        require(
            session.user_id == self.user.id
            and session.chat_id == self.chat_id
            and session.expires_at > now()
            and data.get("kind") == KIND,
            "selection_expired",
        )
        group_id, concert_id = UUID(data["group_id"]), UUID(data["concert_id"])
        group = await Groups(self.store, self.user).access(group_id)
        concert = await self.store.get(Concert, concert_id)
        require(
            concert.group_id == group_id
            and concert.deleted_at is None
            and group.lifecycle_version == data["group_epoch"]
            and concert.lifecycle_version == data["concert_epoch"],
            "selection_expired",
        )
        context = await Groups(self.store, self.user).context(self.chat_id, True)
        require(
            context.group_id == group_id and context.concert_id == concert_id,
            "selection_expired",
        )
        _, version, _ = await Setlists(self.store, self.user).read(
            group_id, concert_id, private=True
        )
        require(version == data["version"], "conflict")
        return session, data

    async def start(self, identifier: UUID) -> tuple[Confirmation, dict[str, Any]]:
        session, data = await self.load(identifier)
        require(data["stage"] == "method", "selection_expired")
        group_id = UUID(data["group_id"])
        counts: Counter[str] = Counter()
        concerts = {
            c.id for c in await self.store.find(Concert, group_id=group_id, deleted_at=None)
        }
        for setlist in await self.store.find(Setlist, group_id=group_id):
            if setlist.concert_id not in concerts or setlist.approved_revision_id is None:
                continue
            revision = await self.store.get(Revision, setlist.approved_revision_id)
            counts.update(
                item["song_id"] for block in revision.snapshot["blocks"] for item in block["items"]
            )
        songs = await Catalog(self.store, self.user).list_songs(group_id)
        songs.sort(key=lambda s: (-counts[str(s.id)], normalize(s.title), str(s.id)))
        require(bool(songs), "selection_empty_catalog")
        # Numbered labels distinguish equal titles and titles resembling controls/commands.
        data["songs"] = [
            {"id": str(s.id), "title": s.title, "version": s.version, "label": f"{i}. {s.title}"}
            for i, s in enumerate(songs, 1)
        ]
        data["stage"] = "choosing"
        self.write(session, data)
        return session, data

    async def save(self, identifier: UUID) -> dict[str, Any]:
        session, data = await self.load(identifier)
        require(data["stage"] == "review" and bool(data["selected"]), "selection_expired")
        catalog = Catalog(self.store, self.user)
        roster = {s["id"]: s for s in data["songs"]}
        for song_id in set(data["selected"]):
            song = await catalog.song(UUID(data["group_id"]), UUID(song_id))
            require(
                not song.archived and song.version == roster[song_id]["version"],
                "selection_catalog_changed",
            )
        await Setlists(self.store, self.user).edit_songs(
            UUID(data["group_id"]),
            UUID(data["concert_id"]),
            data["version"],
            [roster[i]["title"] for i in data["selected"]],
            song_ids=[UUID(i) for i in data["selected"]],
            replace=True,
            private=True,
        )
        await self.store.delete(session)
        return data
