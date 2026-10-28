import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import CatalogImport, Song, SongAlias, User, now
from setlist_bot.domain.importing import ImportRow, ParsedImport, parse_import, read_table
from setlist_bot.domain.rules import normalize, require


@dataclass
class ImportPlan:
    parsed: ParsedImport
    new: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[ImportRow] = field(default_factory=list)
    errors: list[ImportRow] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        content = {
            "new": self.new,
            "duplicates": [(r.number, r.title) for r in self.duplicates],
            "errors": [(r.number, r.error) for r in self.errors],
            "schema": self.parsed.error,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


class Importer:
    def __init__(self, store: Store, user: User, chat_id: int) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id
        self.groups = Groups(store, user)

    async def create(self, group_id: UUID, source: str) -> CatalogImport:
        await self.groups.access(group_id, admin=True)
        read_table(source)
        job = CatalogImport(
            group_id=group_id,
            owner_id=self.user.id,
            chat_id=self.chat_id,
            source=source,
            expires_at=now() + timedelta(minutes=30),
        )
        self.store.add(job)
        await self.store.flush()
        return job

    async def job(self, group_id: UUID, job_id: UUID) -> CatalogImport:
        await self.groups.access(group_id, admin=True)
        job = await self.store.get(CatalogImport, job_id)
        require(
            job.group_id == group_id
            and job.owner_id == self.user.id
            and job.chat_id == self.chat_id
            and job.expires_at > now(),
            "confirmation",
        )
        require(job.status == "pending", "already_done")
        return job

    async def preview(self, group_id: UUID, job_id: UUID) -> ImportPlan:
        job = await self.job(group_id, job_id)
        parsed = parse_import(job.source, job.mapping, job.has_header)
        plan = ImportPlan(parsed)
        songs = await self.store.find(Song, group_id=group_id)
        names: dict[str, set[UUID]] = {}
        for song in songs:
            names.setdefault(normalize(song.title), set()).add(song.id)
        for alias in await self.store.find(SongAlias, group_id=group_id):
            names.setdefault(normalize(alias.title), set()).add(alias.song_id)
        seen: dict[str, dict[str, Any]] = {}
        for row in parsed.rows:
            if row.error:
                plan.errors.append(row)
                continue
            assert row.data is not None
            key = normalize(row.title)
            matches = names.get(key, set())
            if len(matches) > 1:
                row.error = "import_ambiguous"
                plan.errors.append(row)
            elif matches or (key in seen and row.data == seen[key]):
                plan.duplicates.append(row)
            elif key in seen:
                row.error = "import_duplicate_conflict"
                plan.errors.append(row)
            else:
                plan.new.append(row.data)
                seen[key] = row.data
        return plan

    async def commit(self, group_id: UUID, job_id: UUID, fingerprint: str) -> int:
        job = await self.job(group_id, job_id)
        plan = await self.preview(group_id, job_id)
        require(plan.fingerprint == fingerprint, "import_changed")
        require(not plan.parsed.error and not plan.errors and bool(plan.new), "import_fix")
        # RU/EN: Batch import refreshes cards once, not once per imported song.
        catalog = Catalog(self.store, self.user, refresh_cards=False)
        for data in plan.new:
            await catalog.save(group_id, data)
        job.status = "done"
        await queue_card_refreshes(self.store, group_id)
        return len(plan.new)
