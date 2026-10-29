"""RU: Сценарий поиска/отбора. EN: Reuse the existing transactional catalog importer."""

import csv
import io
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.importing import Importer, ImportPlan
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import MusicCache, MusicSearch, Outbox, Song, SongAlias, User, now
from setlist_bot.domain.music_discovery import catalog_titles, default_exclusions
from setlist_bot.domain.rules import clock, require

ACTIVE = frozenset({"searching", "artists", "choosing", "preview"})


class MusicSearches:
    def __init__(self, store: Store, user: User, chat_id: int) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id

    async def create(
        self, group_id: UUID, query: str, *, origin_chat_id: int | None = None
    ) -> MusicSearch:
        group = await Groups(self.store, self.user).access(group_id, admin=True)
        require(self.chat_id == self.user.telegram_id, "private_only")
        require(0 < len(query.strip()) <= 300, "input")
        for old in await self.store.find(MusicSearch, owner_id=self.user.id):
            if old.status in ACTIVE and old.expires_at > now():
                require(old.group_id == group_id and old.query == query.strip(), "music_busy")
                return old
        job = MusicSearch(
            group_id=group_id,
            owner_id=self.user.id,
            chat_id=self.chat_id,
            group_epoch=group.lifecycle_version,
            query=query.strip(),
            expires_at=now() + timedelta(hours=48),
            data={
                "phase": "resolve",
                "raw": [],
                "warnings": [],
                "excluded": [],
                "origin_chat_id": origin_chat_id,
            },
            next_attempt_at=now() + timedelta(seconds=2),
        )
        self.store.add(job)
        await self.store.flush()
        return job

    async def load(self, identifier: UUID) -> MusicSearch:
        # RU: Сначала принадлежность, затем блокировка группы/задания в едином порядке.
        # EN: Do not expose another user's job, even if its UUID is known.
        job = await self.store.get(MusicSearch, identifier)
        require(job.owner_id == self.user.id and job.chat_id == self.chat_id, "forbidden")
        group = await Groups(self.store, self.user).access(job.group_id, admin=True)
        job = await self.store.get(MusicSearch, identifier, lock=True)
        require(
            job.expires_at > now() and job.group_epoch == group.lifecycle_version, "input_expired"
        )
        return job

    @staticmethod
    def write(job: MusicSearch, **changes: Any) -> None:
        # SQLAlchemy tracks assigning a new JSON document, not mutating nested lists.
        job.data = {**job.data, **changes}
        job.version += 1

    async def choose_artist(self, job: MusicSearch, index: int) -> None:
        require(job.status == "artists", "input_expired")
        artists = job.data.get("artists", [])
        require(0 <= index < len(artists), "input")
        job.status = "searching"
        self.write(
            job,
            artist=artists[index],
            phase="songs",
            raw=[],
            warnings=[],
            excluded=[],
            search_started_at=now().isoformat(),
        )
        job.failures, job.next_attempt_at = 0, now() + timedelta(seconds=2)
        caches = await self.store.find(MusicCache, key="public-v1:" + artists[index]["id"])
        if caches and caches[0].expires_at > now():
            cached = caches[0].payload
            job.status = "choosing"
            self.write(
                job,
                tracks=cached["tracks"],
                warnings=cached.get("warnings", []),
                excluded=default_exclusions(cached["tracks"]),
                phase="finish",
            )

    def toggle(self, job: MusicSearch, index: int) -> None:
        require(job.status == "choosing", "input_expired")
        require(0 <= index < len(job.data["tracks"]), "input")
        excluded = set(job.data.get("excluded", []))
        excluded.symmetric_difference_update({index})
        self.write(job, excluded=sorted(excluded))

    def selected(self, job: MusicSearch) -> list[tuple[int, dict[str, Any]]]:
        excluded = set(job.data.get("excluded", []))
        overrides = job.data.get("durations", {})
        return [
            (index, {**row, "seconds": overrides.get(str(index), row["seconds"])})
            for index, row in enumerate(job.data.get("tracks", []))
            if index not in excluded
        ]

    async def preview(self, job: MusicSearch) -> ImportPlan:
        require(job.status == "choosing", "input_expired")
        rows = self.selected(job)
        require(bool(rows), "music_empty")
        require(all(row["seconds"] for _, row in rows), "music_missing_duration")
        songs = await self.store.find(Song, group_id=job.group_id)
        aliases = await self.store.find(SongAlias, group_id=job.group_id)
        titles = catalog_titles(
            [row for _, row in rows],
            [song.title for song in songs] + [alias.title for alias in aliases],
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
        writer.writerow(["Название", "Длительность", "Цвета", "BPM", "Комментарий"])
        for (_, row), title in zip(rows, titles, strict=True):
            writer.writerow([title, clock(row["seconds"]), "", row["bpm"] or "", ""])
        importer = Importer(self.store, self.user, self.chat_id)
        pending = await importer.create(job.group_id, buffer.getvalue())
        plan = await importer.preview(job.group_id, pending.id)
        require(not plan.errors and not plan.parsed.error, "import_fix")
        job.status = "preview"
        self.write(
            job,
            import_id=str(pending.id),
            fingerprint=plan.fingerprint,
            preview_token=secrets.token_hex(4),
            new_count=len(plan.new),
            duplicate_count=len(plan.duplicates),
            preview_rows=plan.new,
            page=0,
        )
        return plan

    async def commit(self, job: MusicSearch, token: str) -> int:
        require(job.status == "preview" and token == job.data.get("preview_token"), "already_done")
        importer = Importer(self.store, self.user, self.chat_id)
        count = await importer.commit(
            job.group_id, UUID(job.data["import_id"]), job.data["fingerprint"]
        )
        job.status = "done"
        self.write(job, imported_count=count)
        return count


async def notify_search(store: Store, job: MusicSearch, *, debounce: bool = False) -> None:
    """RU/EN: Persist the transition and its notification in the same transaction."""
    destination = (
        job.data.get("origin_chat_id") if job.status in ("searching", "artists") else None
    ) or job.chat_id
    payload = {"job_id": str(job.id), "status": job.status, "version": job.version}
    due = now() + timedelta(milliseconds=300) if debounce else now()
    for event in await store.find(
        Outbox, group_id=job.group_id, chat_id=destination, support_kind="music", sent=False
    ):
        if event.payload.get("job_id") == str(job.id):
            event.payload = payload
            event.next_attempt_at = max(event.next_attempt_at or due, due)
            return
    store.add(
        Outbox(
            chat_id=destination,
            group_id=job.group_id,
            text="",
            support_kind="music",
            payload=payload,
            next_attempt_at=due,
        )
    )
