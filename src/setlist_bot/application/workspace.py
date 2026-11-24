"""Navigation for the member-facing workspace, independent of the client."""

from typing import Any
from uuid import UUID

from setlist_bot.application.catalog import SONG_FIELDS, Catalog, song_data
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.workspace_setlists import revision_token
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    CatalogProposal,
    Concert,
    Group,
    Membership,
    Song,
    SongAlias,
    User,
)
from setlist_bot.domain.rules import AppError, clock, flat_content, is_pause, totals

PAGE_SIZE = 20


async def can_open_draft(groups: Groups, group_id: UUID) -> bool:
    try:
        await groups.can_suggest(group_id)
    except AppError as exc:
        if exc.code != "admin_suggestions_only":
            raise
        return False
    return True


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
        can_edit = await can_open_draft(groups, group_id)
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

    async def performance(self, group_id: UUID, performance_id: UUID) -> dict[str, Any]:
        groups = Groups(self.store, self.user)
        group = await groups.access(group_id)
        snapshot, version, approved = await Setlists(self.store, self.user).read(
            group_id, performance_id
        )
        concert = await self.store.get(Concert, performance_id)
        member = (
            await self.store.find(
                Membership, group_id=group_id, user_id=self.user.id, status="active"
            )
        )[0]
        items = flat_content(snapshot)["blocks"][0]["items"]
        return {
            "language": self.user.language,
            "band": group.name,
            "performance": {
                "id": str(concert.id),
                "date": concert.date.isoformat(),
                "title": concert.title,
                "city": concert.city,
                "type": concert.performance_type,
                "info": concert.extra_info,
                "planned_seconds": concert.performance_duration_seconds,
            },
            "can_edit_draft": await can_open_draft(groups, group_id),
            "admin": member.role == "admin",
            "setlist": {
                "approved": approved,
                "version": version,
                "token": revision_token(group, concert, snapshot),
                "seconds": totals(snapshot)[2],
                "entries": [
                    {
                        "kind": "pause",
                        "duration_seconds": item["duration_seconds"],
                        "label": item.get("pause_label"),
                    }
                    if is_pause(item)
                    else {
                        "kind": "song",
                        "duration_seconds": item["duration_seconds"],
                        "title": item["title"],
                        "bpm": item.get("bpm"),
                        "lights": item.get("lights"),
                        "comment": item.get("comment"),
                    }
                    for item in items
                ],
            },
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
