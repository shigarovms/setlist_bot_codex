from copy import deepcopy
from datetime import date
from typing import Any
from uuid import UUID

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import (
    Block,
    Concert,
    Item,
    PersonalDraft,
    Revision,
    Setlist,
    Song,
    Submission,
    User,
)
from setlist_bot.domain.rules import pause_content, pause_seconds, require

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
                        {"song_id": str(item.song_id), "pause_before": item.pause_before}
                        for item in sorted(items, key=lambda item: item.position)
                    ],
                }
            )
        return result

    async def _snapshot(self, group_id: UUID, content: Document) -> Document:
        snapshot = pause_content(content)
        for block in snapshot["blocks"]:
            for item in block["items"]:
                song = await self.store.get(Song, UUID(item["song_id"]))
                require(song.group_id == group_id, "not_found")
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
            return deepcopy(revision.snapshot), setlist.version, True
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
        content = pause_content(draft.content) if draft else await self._working(setlist)
        blocks = content["blocks"]
        if operation == "default_pause":
            content["default_pause_seconds"] = pause_seconds(args[0])
        elif operation in ("block", "encore"):
            title = " ".join(args)
            require(bool(title) and len(title) <= 200 and len(blocks) < 20, "setlist_input")
            kind = (
                "encore"
                if operation == "encore" or title.casefold() in ("бис", "encore")
                else "regular"
            )
            require(
                kind != "encore" or not any(b["kind"] == "encore" for b in blocks), "encore_exists"
            )
            blocks.append({"title": title, "kind": kind, "break_seconds": 0, "items": []})
        elif operation == "remove_block":
            index = int(args[0]) - 1
            require(0 < index < len(blocks), "setlist_input")
            blocks.pop(index)
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
            elif operation == "break":
                seconds = int(args[1])
                require(0 <= seconds <= 86400 and index < len(blocks) - 1, "setlist_input")
                block["break_seconds"] = seconds
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
            draft.content = content
            draft.version += 1
        else:
            await self._persist(setlist, content)
            await self._record(setlist, operation, content)

    async def _persist(self, setlist: Setlist, content: Document) -> None:
        content = pause_content(content)
        setlist.default_pause_seconds = content["default_pause_seconds"]
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
                song = await self.store.get(Song, UUID(item["song_id"]))
                require(song.group_id == setlist.group_id, "not_found")
                self.store.add(
                    Item(
                        group_id=setlist.group_id,
                        block_id=block.id,
                        song_id=song.id,
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
    ) -> None:
        """Resolve every input before writing a single atomic setlist revision."""
        setlist = await self.load(group_id, concert_id)
        draft = await self._draft(setlist) if private else None
        require((draft.version if draft else setlist.version) == expected, "conflict")
        require(private or not setlist.approved, "approved")
        content = pause_content(draft.content) if draft else await self._working(setlist)
        catalog = Catalog(self.store, self.user)
        if song_ids is None:
            songs = await catalog.resolve_list(group_id, titles)
        else:
            require(1 <= len(song_ids) <= 200, "song_list_input")
            songs = [await catalog.song(group_id, identifier) for identifier in song_ids]
            require(all(not song.archived for song in songs), "selection_catalog_changed")
        items = [{"song_id": str(song.id), "pause_before": None} for song in songs]
        if replace:
            content["blocks"] = [{"title": "", "kind": "main", "break_seconds": 0, "items": items}]
        else:
            require(1 <= block <= len(content["blocks"]), "setlist_input")
            require(
                sum(len(b["items"]) for b in content["blocks"]) + len(items) <= 200, "setlist_input"
            )
            content["blocks"][block - 1]["items"].extend(items)
        if draft:
            draft.content = content
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
        await self.store.flush()
        return revision

    async def approve(self, group_id: UUID, concert_id: UUID, expected: int) -> None:
        await self.groups.access(group_id, admin=True)
        setlist = await self.load(group_id, concert_id)
        require(setlist.version == expected, "conflict")
        require(not setlist.approved, "approved")
        content = await self._working(setlist)
        require(any(b["items"] for b in content["blocks"]), "empty_setlist")
        revision = await self._record(setlist, "approved", content)
        setlist.approved_revision_id = revision.id
        setlist.approved = True

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
