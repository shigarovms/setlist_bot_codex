from typing import Any
from uuid import UUID

from rapidfuzz.fuzz import WRatio

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import CatalogProposal, Membership, Song, SongAlias, User
from setlist_bot.domain.rules import SongListError, normalize, require, search_key, validate_song

SONG_FIELDS = ("title", "duration_seconds", "bpm", "lights", "comment")


class Catalog:
    def __init__(self, store: Store, user: User, *, refresh_cards: bool = True) -> None:
        self.store, self.user = store, user
        self.refresh_cards = refresh_cards
        self.groups = Groups(store, user)

    async def list_songs(self, group_id: UUID, *, archived: bool = False) -> list[Song]:
        await self.groups.access(group_id)
        return sorted(
            await self.store.find(Song, group_id=group_id, archived=archived),
            key=lambda song: normalize(song.title),
        )

    async def song(self, group_id: UUID, song_id: UUID) -> Song:
        await self.groups.access(group_id)
        song = await self.store.get(Song, song_id)
        require(song.group_id == group_id, "not_found")
        return song

    async def save(
        self,
        group_id: UUID,
        data: dict[str, Any],
        song_id: UUID | None = None,
        expected: int | None = None,
    ) -> Song | CatalogProposal:
        await self.groups.access(group_id)
        validate_song(data)
        require(set(data) <= set(SONG_FIELDS), "song_input")
        song = await self.song(group_id, song_id) if song_id else None
        if song:
            require(song.version == expected, "conflict")
        member = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
        if member.role != "admin":
            proposal = CatalogProposal(
                group_id=group_id,
                author_id=self.user.id,
                payload=data,
                song_id=song_id,
                expected_version=expected,
            )
            self.store.add(proposal)
            return proposal
        return await self._save(group_id, data, song)

    async def _save(self, group_id: UUID, data: dict[str, Any], song: Song | None) -> Song:
        created = song is None
        if song is None:
            song = Song(group_id=group_id, **data)
            self.store.add(song)
        else:
            for key in SONG_FIELDS:
                setattr(song, key, data.get(key, "" if key in ("lights", "comment") else None))
            song.version += 1
        await self.store.flush()
        record_activity(
            self.store,
            self.user,
            "song_created" if created else "song_changed",
            song.id,
            group_id=str(group_id),
            version=song.version,
            fields=sorted(data),
        )
        if self.refresh_cards:
            await queue_card_refreshes(self.store, group_id)
        return song

    async def archive(self, group_id: UUID, song_id: UUID, expected: int, archived: bool) -> None:
        await self.groups.access(group_id, admin=True)
        song = await self.song(group_id, song_id)
        require(song.version == expected, "conflict")
        song.archived, song.version = archived, song.version + 1
        record_activity(
            self.store,
            self.user,
            "song_archived",
            song_id,
            group_id=str(group_id),
            archived=archived,
        )

    async def alias(self, group_id: UUID, song_id: UUID, title: str) -> SongAlias | CatalogProposal:
        await self.groups.access(group_id)
        song = await self.song(group_id, song_id)
        require(0 < len(title.strip()) <= 200, "song_input")
        # Collisions are never silently accepted: choose a distinct alias in this first version.
        for candidate in await self.store.find(Song, group_id=group_id):
            if candidate.id != song.id:
                require(normalize(candidate.title) != normalize(title), "alias_collision")
        for alias in await self.store.find(SongAlias, group_id=group_id):
            require(normalize(alias.title) != normalize(title), "alias_collision")
        membership = (await self.store.find(Membership, group_id=group_id, user_id=self.user.id))[0]
        result: SongAlias | CatalogProposal
        if membership.role == "admin":
            result = SongAlias(group_id=group_id, song_id=song_id, title=title.strip())
        else:
            result = CatalogProposal(
                group_id=group_id,
                author_id=self.user.id,
                payload={"alias": title.strip()},
                song_id=song_id,
                expected_version=song.version,
            )
        self.store.add(result)
        return result

    async def decide(self, group_id: UUID, proposal_id: UUID, approve: bool) -> None:
        await self.groups.access(group_id, admin=True)
        proposal = await self.store.get(CatalogProposal, proposal_id)
        require(proposal.group_id == group_id, "not_found")
        require(proposal.status == "pending", "already_done")
        if approve:
            song = await self.song(group_id, proposal.song_id) if proposal.song_id else None
            if song:
                require(song.version == proposal.expected_version, "conflict")
            if "alias" in proposal.payload:
                require(song is not None, "not_found")
                assert song is not None
                await self.alias(group_id, song.id, proposal.payload["alias"])
            else:
                validate_song(proposal.payload)
                await self._save(group_id, proposal.payload, song)
        proposal.status = "approved" if approve else "rejected"

    async def search(self, group_id: UUID, query: str) -> list[Song]:
        songs = await self.list_songs(group_id)
        aliases = await self.store.find(SongAlias, group_id=group_id)
        return self._matches(songs, aliases, query)

    async def resolve_list(self, group_id: UUID, titles: list[str]) -> list[Song]:
        require(
            1 <= len(titles) <= 200 and all(0 < len(t) <= 200 for t in titles), "song_list_input"
        )
        songs = await self.list_songs(group_id)
        aliases = await self.store.find(SongAlias, group_id=group_id)
        result = []
        issues = []
        for number, title in enumerate(titles, 1):
            matches = self._matches(songs, aliases, title)
            if len(matches) == 1:
                result.append(matches[0])
            else:
                issues.append((number, title, [s.title for s in matches]))
        if issues:
            raise SongListError(issues)
        return result

    @staticmethod
    def _matches(songs: list[Song], aliases: list[SongAlias], query: str) -> list[Song]:
        names = {song.id: [song.title] for song in songs}
        for alias in aliases:
            if alias.song_id in names:
                names[alias.song_id].append(alias.title)
        key = search_key(query)
        exact = [song for song in songs if any(search_key(n) == key for n in names[song.id])]
        if exact:
            return exact
        scores = [(max(WRatio(key, search_key(n)) for n in names[song.id]), song) for song in songs]
        return [
            song
            for score, song in sorted(scores, key=lambda pair: pair[0], reverse=True)
            if score >= 70
        ][:10]
