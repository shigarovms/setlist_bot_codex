"""Navigation for the member-facing workspace, independent of the client."""

from typing import Any
from uuid import UUID

from setlist_bot.application.catalog import SONG_FIELDS, Catalog, song_data
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import CatalogProposal, Group, Membership, Song, SongAlias, User
from setlist_bot.domain.rules import AppError, clock

PAGE_SIZE = 20


class Workspace:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def bands(self) -> dict[str, Any]:
        memberships = await self.store.find(Membership, user_id=self.user.id, status="active")
        groups = await self.store.get_many(Group, {member.group_id for member in memberships})
        return {
            "language": self.user.language,
            "bands": [
                {"id": str(group.id), "name": group.name}
                for group in sorted(groups, key=lambda row: (row.name.casefold(), row.id))
                if group.deleted_at is None
            ],
        }

    async def performances(self, group_id: UUID, *, past: bool, page: int) -> dict[str, Any]:
        groups = Groups(self.store, self.user)
        group = await groups.access(group_id)
        try:
            await groups.can_suggest(group_id)
            can_edit = True
        except AppError as exc:
            if exc.code != "admin_suggestions_only":
                raise
            can_edit = False
        rows = await self.store.concerts_for_user(
            self.user.id,
            today(),
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE + 1,
            past=past,
            group_id=group_id,
        )
        return {
            "language": self.user.language,
            "band": group.name,
            "can_edit_draft": can_edit,
            "has_next": len(rows) > PAGE_SIZE,
            "performances": [
                {
                    "id": str(row.id),
                    "date": row.date.isoformat(),
                    "title": row.title,
                    "city": row.city,
                }
                for row, _ in rows[:PAGE_SIZE]
            ],
        }

    async def repertoire(
        self, group_id: UUID, *, archived: bool, page: int, query: str, song_id: UUID | None
    ) -> dict[str, Any]:
        group = await Groups(self.store, self.user).access(group_id)
        catalog = Catalog(self.store, self.user)
        membership = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
        context = {
            "language": self.user.language,
            "band": group.name,
            "admin": membership.role == "admin",
        }
        if song_id is not None:
            song = await catalog.song(group_id, song_id)
            aliases = await self.store.find(SongAlias, group_id=group_id, song_id=song.id)
            return {**context, "song": {**song_data(song, aliases), "archived": song.archived}}
        songs = (
            await catalog.search(group_id, query, archived=archived)
            if query
            else await catalog.list_songs(group_id, archived=archived)
        )
        start = page * PAGE_SIZE
        return {
            **context,
            "has_next": start + PAGE_SIZE < len(songs),
            "songs": [
                {"id": str(song.id), "title": song.title, "duration": clock(song.duration_seconds)}
                for song in songs[start : start + PAGE_SIZE]
            ],
        }

    async def proposals(self, group_id: UUID, page: int) -> dict[str, Any]:
        groups = Groups(self.store, self.user)
        group = await groups.access(group_id, admin=True)
        pending = await self.store.find(CatalogProposal, group_id=group_id, status="pending")
        pending.sort(key=lambda proposal: proposal.id)
        start = page * PAGE_SIZE
        rows = pending[start : start + PAGE_SIZE]
        songs = await self.store.get_many(Song, {row.song_id for row in rows if row.song_id})
        titles = {song.id: song.title for song in songs if song.group_id == group_id}
        authors = {
            user_id: await groups.name(group_id, user_id) for user_id in {r.author_id for r in rows}
        }
        return {
            "language": self.user.language,
            "band": group.name,
            "has_next": start + PAGE_SIZE < len(pending),
            "proposals": [
                {
                    "id": str(row.id),
                    "author": authors[row.author_id],
                    "original_title": titles.get(row.song_id) if row.song_id else None,
                    "fields": {
                        key: row.payload[key]
                        for key in (*SONG_FIELDS, "alias", "aliases")
                        if key in row.payload
                    },
                }
                for row in rows
            ],
        }
