"""Navigation for the member-facing workspace, independent of the client."""

from typing import Any
from uuid import UUID

from setlist_bot.application.catalog import Catalog, song_data
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import Group, Membership, SongAlias, User
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
        context = {"language": self.user.language, "band": group.name}
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
