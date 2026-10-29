"""RU: Сценарии сет-листов; транспорт Telegram и конкретная БД здесь не нужны.

Рабочий список хранится нормализованными строками, личный черновик — JSON только
своего владельца, а Revision — неизменяемый снимок истории. Нельзя использовать
личный черновик в общем чате до явной публикации. expected — версия, которую видел
пользователь: сравнение не позволяет старой кнопке затереть новую правку.

EN: Setlist use cases depend on a persistence port, not Telegram or SQLAlchemy.
Working entries are rows, personal drafts are owner-scoped JSON, and revisions are
immutable history. The expected version prevents lost updates from stale actions.
"""

from datetime import date
from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.support import Support
from setlist_bot.domain.display import entry_labels
from setlist_bot.domain.entities import (
    Block,
    Concert,
    Group,
    Item,
    PersonalDraft,
    Revision,
    Setlist,
    Song,
    Submission,
    User,
)
from setlist_bot.domain.rules import (
    SongListError,
    flat_content,
    is_pause,
    parse_pause,
    pause_item,
    pause_seconds,
    require,
)

Document = dict[str, Any]


class Setlists:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)

    async def create(self, group_id: UUID, when: date, title: str = "") -> Concert:
        group = await self.groups.access(group_id, admin=True)
        require(len(title) <= 200, "concert_input")
        concert = Concert(group_id=group_id, date=when, title=title)
        self.store.add(concert)
        await self.store.flush()
        setlist = Setlist(
            group_id=group_id,
            concert_id=concert.id,
            default_pause_seconds=group.default_pause_seconds,
        )
        self.store.add(setlist)
        await self.store.flush()
        self.store.add(Block(group_id=group_id, setlist_id=setlist.id, position=1, kind="main"))
        await self.store.flush()
        await self._record(setlist, "created", await self._working(setlist), increment=False)
        support = Support(self.store, self.user)
        await support.notify(
            f"Создано выступление: {when:%d.%m.%Y} {title}\n"
            f"Коллектив: {group.name}\nАвтор: {support.actor}"
        )
        return concert

    async def load(self, group_id: UUID, concert_id: UUID) -> Setlist:
        await self.groups.access(group_id)
        concert = await self.store.get(Concert, concert_id)
        require(concert.group_id == group_id, "not_found")
        require(concert.deleted_at is None, "in_trash")
        return (await self.store.find(Setlist, group_id=group_id, concert_id=concert_id))[0]

    async def _working(self, setlist: Setlist) -> Document:
        result: Document = {"default_pause_seconds": setlist.default_pause_seconds, "blocks": []}
        blocks = await self.store.find(Block, setlist_id=setlist.id, group_id=setlist.group_id)
        for block in sorted(blocks, key=lambda b: b.position):
            items = await self.store.find(Item, block_id=block.id, group_id=setlist.group_id)
            result["blocks"].append(
                {
                    "title": block.title,
                    "kind": block.kind,
                    "break_seconds": block.break_seconds,
                    "items": [
                        (
                            pause_item(item.pause_duration_seconds)
                            if item.pause_duration_seconds is not None
                            else {"song_id": str(item.song_id), "pause_before": item.pause_before}
                        )
                        for item in sorted(items, key=lambda item: item.position)
                    ],
                }
            )
        return flat_content(result)

    async def _snapshot(self, group_id: UUID, content: Document) -> Document:
        snapshot = flat_content(content)
        # RU: Повторы остаются в списке, но каждую уникальную песню читаем только один раз.
        # EN: Preserve repeated entries while loading each distinct song only once.
        identifiers = {
            UUID(item["song_id"])
            for block in snapshot["blocks"]
            for item in block["items"]
            if not is_pause(item)
        }
        songs = {song.id: song for song in await self.store.get_many(Song, identifiers)}
        for block in snapshot["blocks"]:
            for item in block["items"]:
                if is_pause(item):
                    continue
                song = songs.get(UUID(item["song_id"]))
                require(song is not None and song.group_id == group_id, "not_found")
                assert song is not None
                item.update(
                    title=song.title,
                    duration_seconds=song.duration_seconds,
                    bpm=song.bpm,
                    lights=song.lights,
                    comment=song.comment,
                    song_version=song.version,
                )
        return snapshot

    async def _draft(self, setlist: Setlist) -> PersonalDraft:
        drafts = await self.store.find(
            PersonalDraft,
            group_id=setlist.group_id,
            concert_id=setlist.concert_id,
            owner_id=self.user.id,
        )
        if drafts:
            return drafts[0]
        draft = PersonalDraft(
            group_id=setlist.group_id,
            concert_id=setlist.concert_id,
            owner_id=self.user.id,
            content=await self._working(setlist),
        )
        self.store.add(draft)
        await self.store.flush()
        return draft

    async def read(
        self, group_id: UUID, concert_id: UUID, *, private: bool = False
    ) -> tuple[Document, int, bool]:
        setlist = await self.load(group_id, concert_id)
        if private:
            draft = await self._draft(setlist)
            return await self._snapshot(group_id, draft.content), draft.version, False
        if setlist.approved:
            assert setlist.approved_revision_id is not None
            revision = await self.store.get(Revision, setlist.approved_revision_id)
            return flat_content(revision.snapshot), setlist.version, True
        return await self._snapshot(group_id, await self._working(setlist)), setlist.version, False

    async def edit(
        self,
        group_id: UUID,
        concert_id: UUID,
        expected: int,
        operation: str,
        args: list[str],
        *,
        private: bool = False,
    ) -> None:
        setlist = await self.load(group_id, concert_id)
        draft = await self._draft(setlist) if private else None
        require((draft.version if draft else setlist.version) == expected, "conflict")
        require(private or not setlist.approved, "approved")
        content = flat_content(draft.content) if draft else await self._working(setlist)
        blocks = content["blocks"]
        if operation == "default_pause":
            content["default_pause_seconds"] = pause_seconds(args[0])
        else:
            index = int(args[0]) - 1
            require(0 <= index < len(blocks), "setlist_input")
            block = blocks[index]
            items = block["items"]
            if operation == "add":
                song = await self.store.get(Song, UUID(args[1]))
                require(song.group_id == group_id, "not_found")
                require(not song.archived, "archived")
                require(sum(len(b["items"]) for b in blocks) < 200, "setlist_input")
                items.append({"song_id": str(song.id), "pause_before": None})
            else:
                pos = int(args[1]) - 1
                require(0 <= pos < len(items), "setlist_input")
                if operation == "remove":
                    items.pop(pos)
                elif operation == "move":
                    target = int(args[2]) - 1
                    require(0 <= target < len(items), "setlist_input")
                    items.insert(target, items.pop(pos))
                elif operation == "pause":
                    items[pos]["pause_before"] = (
                        None if args[2] == "default" else pause_seconds(args[2])
                    )
                else:
                    require(False, "setlist_input")
        blocks[-1]["break_seconds"] = 0
        if draft:
            draft.content = flat_content(content)
            draft.version += 1
        else:
            await self._persist(setlist, content)
            await self._record(setlist, operation, content)

    async def _persist(self, setlist: Setlist, content: Document) -> None:
        content = flat_content(content)
        setlist.default_pause_seconds = content["default_pause_seconds"]
        identifiers = {
            UUID(item["song_id"])
            for block in content["blocks"]
            for item in block["items"]
            if not is_pause(item)
        }
        songs = {song.id: song for song in await self.store.get_many(Song, identifiers)}
        # RU: Проверяем коллектив до удаления старых позиций; неизвестный ID не становится паузой.
        # EN: Validate tenant ownership before replacing rows; a missing ID is never a pause.
        require(
            set(songs) == identifiers
            and all(song.group_id == setlist.group_id for song in songs.values()),
            "not_found",
        )
        blocks = await self.store.find(Block, setlist_id=setlist.id)
        for block in blocks:
            for item in await self.store.find(Item, block_id=block.id):
                await self.store.delete(item)
            await self.store.flush()
            await self.store.delete(block)
        await self.store.flush()
        for number, data in enumerate(content["blocks"], 1):
            block = Block(
                group_id=setlist.group_id,
                setlist_id=setlist.id,
                position=number,
                title=data["title"],
                break_seconds=data["break_seconds"],
                kind=data["kind"],
            )
            self.store.add(block)
            await self.store.flush()
            for position, item in enumerate(data["items"], 1):
                song = None if is_pause(item) else songs[UUID(item["song_id"])]
                require(song is None or song.group_id == setlist.group_id, "not_found")
                self.store.add(
                    Item(
                        group_id=setlist.group_id,
                        block_id=block.id,
                        song_id=song.id if song else None,
                        pause_duration_seconds=item["duration_seconds"] if is_pause(item) else None,
                        position=position,
                        pause_before=item["pause_before"],
                    )
                )
            await self.store.flush()

    async def edit_songs(
        self,
        group_id: UUID,
        concert_id: UUID,
        expected: int,
        titles: list[str],
        *,
        replace: bool = False,
        block: int = 1,
        private: bool = False,
        song_ids: list[UUID] | None = None,
        ordered: list[UUID | int] | None = None,
    ) -> None:
        """Resolve every input before writing a single atomic setlist revision."""
        setlist = await self.load(group_id, concert_id)
        draft = await self._draft(setlist) if private else None
        require((draft.version if draft else setlist.version) == expected, "conflict")
        require(private or not setlist.approved, "approved")
        content = flat_content(draft.content) if draft else await self._working(setlist)
        catalog = Catalog(self.store, self.user)
        if ordered is None and song_ids is None:
            require(1 <= len(titles) <= 200, "song_list_input")
            pauses = [parse_pause(title) for title in titles]
            positions = [i for i, pause in enumerate(pauses) if pause is None]
            try:
                songs = (
                    await catalog.resolve_list(group_id, [titles[i] for i in positions])
                    if positions
                    else []
                )
            except SongListError as exc:
                raise SongListError(
                    [
                        (positions[n - 1] + 1, title, candidates)
                        for n, title, candidates in exc.issues
                    ]
                ) from exc
            iterator = iter(songs)
            items = [
                pause_item(pause)
                if pause is not None
                else {"song_id": str(next(iterator).id), "pause_before": None}
                for pause in pauses
            ]
        else:
            entries = ordered if ordered is not None else list(song_ids or [])
            require(1 <= len(entries) <= 200, "song_list_input")
            items = []
            for entry in entries:
                if isinstance(entry, int):
                    items.append(pause_item(entry))
                else:
                    song = await catalog.song(group_id, entry)
                    require(not song.archived, "selection_catalog_changed")
                    items.append({"song_id": str(song.id), "pause_before": None})
        if replace:
            content["blocks"] = [{"title": "", "kind": "main", "break_seconds": 0, "items": items}]
        else:
            require(1 <= block <= len(content["blocks"]), "setlist_input")
            require(
                sum(len(b["items"]) for b in content["blocks"]) + len(items) <= 200, "setlist_input"
            )
            content["blocks"][block - 1]["items"].extend(items)
        if draft:
            draft.content = flat_content(content)
            draft.version += 1
        else:
            await self._persist(setlist, content)
            await self._record(setlist, "suggestion" if replace else "add_list", content)

    async def _record(
        self,
        setlist: Setlist,
        action: str,
        content: Document,
        *,
        increment: bool = True,
        proposer_id: UUID | None = None,
    ) -> Revision:
        if increment:
            setlist.version += 1
        suggestion_number = None
        if action == "suggestion":
            proposer_id = self.user.id
        if proposer_id is not None:
            authored = await self.store.find(
                Revision, setlist_id=setlist.id, proposer_id=proposer_id
            )
            suggestion_number = 1 + max((r.suggestion_number or 0 for r in authored), default=0)
        revision = Revision(
            group_id=setlist.group_id,
            setlist_id=setlist.id,
            author_id=self.user.id,
            number=setlist.version,
            action=action,
            snapshot=await self._snapshot(setlist.group_id, content),
            suggestion_number=suggestion_number,
            proposer_id=proposer_id,
        )
        self.store.add(revision)
        record_activity(
            self.store,
            self.user,
            "setlist_" + action,
            setlist.concert_id,
            version=setlist.version,
            group_id=str(setlist.group_id),
        )
        await self.store.flush()
        return revision

    async def approve(self, group_id: UUID, concert_id: UUID, expected: int) -> None:
        await self.groups.access(group_id, admin=True)
        setlist = await self.load(group_id, concert_id)
        require(setlist.version == expected, "conflict")
        require(not setlist.approved, "approved")
        content = await self._working(setlist)
        require(any(b["items"] for b in content["blocks"]), "empty_setlist")
        content["approved_by"] = await self.groups.name(group_id)
        revision = await self._record(setlist, "approved", content)
        setlist.approved_revision_id = revision.id
        setlist.approved = True
        concert = await self.store.get(Concert, concert_id)
        group = await self.store.get(Group, group_id)
        support = Support(self.store, self.user)
        lines: list[str] = []
        for block in revision.snapshot["blocks"]:
            for label, item in zip(entry_labels(block["items"]), block["items"], strict=True):
                seconds = item["duration_seconds"]
                lines.append(f"{label}. {item['title']}  {seconds // 60:02d}:{seconds % 60:02d}")
        await support.notify(
            f"Утверждён сет-лист · Версия {revision.number}\n"
            f"{group.name} · {concert.date:%d.%m.%Y} {concert.title}\n"
            f"Утвердил: {support.actor}\n\n" + "\n".join(lines)
        )

    async def reopen(self, group_id: UUID, concert_id: UUID, expected: int) -> None:
        await self.groups.access(group_id, admin=True)
        setlist = await self.load(group_id, concert_id)
        require(setlist.version == expected, "conflict")
        require(setlist.approved, "not_approved")
        setlist.approved = False
        await self._record(setlist, "reopened", await self._working(setlist))

    async def history(self, group_id: UUID, concert_id: UUID) -> list[Revision]:
        setlist = await self.load(group_id, concert_id)
        return sorted(
            await self.store.find(Revision, group_id=group_id, setlist_id=setlist.id),
            key=lambda r: r.number,
            reverse=True,
        )

    async def restore(
        self, group_id: UUID, concert_id: UUID, revision_id: UUID, expected: int
    ) -> None:
        setlist = await self.load(group_id, concert_id)
        require(not setlist.approved, "approved")
        require(setlist.version == expected, "conflict")
        revision = await self.store.get(Revision, revision_id)
        require(revision.group_id == group_id and revision.setlist_id == setlist.id, "not_found")
        await self._persist(setlist, revision.snapshot)
        await self._record(setlist, "restored", revision.snapshot)

    async def publish(self, group_id: UUID, concert_id: UUID) -> Submission:
        setlist = await self.load(group_id, concert_id)
        draft = await self._draft(setlist)
        submission = Submission(
            group_id=group_id,
            concert_id=concert_id,
            author_id=self.user.id,
            snapshot=await self._snapshot(group_id, draft.content),
        )
        self.store.add(submission)
        return submission

    async def apply(
        self, group_id: UUID, concert_id: UUID, submission_id: UUID, expected: int
    ) -> None:
        setlist = await self.load(group_id, concert_id)
        require(not setlist.approved, "approved")
        require(setlist.version == expected, "conflict")
        submission = await self.store.get(Submission, submission_id)
        require(
            submission.group_id == group_id and submission.concert_id == concert_id, "not_found"
        )
        require(not submission.applied, "already_done")
        for block in submission.snapshot["blocks"]:
            for item in block["items"]:
                if is_pause(item):
                    continue
                song = await self.store.get(Song, UUID(item["song_id"]))
                require(song.group_id == group_id, "not_found")
                require(
                    song.version == item["song_version"] and not song.archived, "catalog_changed"
                )
        await self._persist(setlist, submission.snapshot)
        await self._record(
            setlist, "proposal_applied", submission.snapshot, proposer_id=submission.author_id
        )
        submission.applied = True
