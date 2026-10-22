import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.domain import entities as e
from setlist_bot.domain.rules import require

RETENTION = timedelta(days=30)


class Trash:
    def __init__(self, store: Store, user: e.User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)

    async def group_fingerprint(self, group_id: UUID) -> str:
        group = await self.groups.access(group_id, admin=True)
        parts = [asdict(group)]
        for kind in (
            e.Membership,
            e.Song,
            e.SongAlias,
            e.CatalogProposal,
            e.Concert,
            e.Setlist,
            e.Block,
            e.Item,
            e.PersonalDraft,
            e.Submission,
        ):
            rows = await self.store.find(kind, group_id=group_id)
            parts.extend(asdict(row) for row in sorted(rows, key=lambda row: row.id))
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    async def delete_group(self, group_id: UUID, fingerprint: str) -> e.Group:
        group = await self.groups.access(group_id, admin=True)
        require(await self.group_fingerprint(group_id) == fingerprint, "delete_changed")
        group.deleted_at = e.now()
        group.lifecycle_version += 1
        bindings = await self.store.find(e.ChatBinding, group_id=group_id)
        for binding in bindings:
            group.deleted_chat_id = binding.chat_id
            await self._cancel_outbox(group_id, chat_id=binding.chat_id)
            await self.store.delete(binding)
        await self._clear_contexts(group_id)
        return group

    async def restore_group(self, group_id: UUID, epoch: int) -> e.Group:
        # Chat creation takes the same lock before it can claim the released chat.
        group = await self.store.get(e.Group, group_id)
        if group.deleted_chat_id is not None:
            await self.store.lock_chat(group.deleted_chat_id)
        group = await self.groups.access(group_id, admin=True, deleted=True)
        require(group.deleted_at is not None and group.lifecycle_version == epoch, "confirmation")
        assert group.deleted_at is not None
        require(group.deleted_at + RETENTION > e.now(), "trash_expired")
        if group.deleted_chat_id is not None:
            require(
                not await self.store.find(e.ChatBinding, chat_id=group.deleted_chat_id),
                "chat_taken",
            )
            self.store.add(e.ChatBinding(group_id=group.id, chat_id=group.deleted_chat_id))
        group.deleted_at, group.deleted_chat_id = None, None
        group.lifecycle_version += 1
        return group

    async def delete_concert(self, group_id: UUID, concert_id: UUID, fingerprint: str) -> e.Concert:
        await self.groups.access(group_id, admin=True)
        concert = await self.store.get(e.Concert, concert_id)
        require(concert.group_id == group_id, "not_found")
        require(concert.deleted_at is None, "in_trash")
        require(
            await self.concert_fingerprint(group_id, concert_id) == fingerprint, "delete_changed"
        )
        concert.deleted_at = e.now()
        concert.lifecycle_version += 1
        await self._cancel_outbox(group_id, concert_id=concert_id)
        await self._clear_contexts(group_id, concert_id)
        return concert

    async def concert_fingerprint(self, group_id: UUID, concert_id: UUID) -> str:
        await self.groups.access(group_id, admin=True)
        concert = await self.store.get(e.Concert, concert_id)
        require(concert.group_id == group_id and concert.deleted_at is None, "not_found")
        parts = [asdict(concert)]
        for kind in (e.Setlist, e.PersonalDraft, e.Submission):
            parts.extend(
                asdict(row)
                for row in sorted(
                    await self.store.find(kind, group_id=group_id, concert_id=concert_id),
                    key=lambda r: r.id,
                )
            )
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    async def restore_concert(self, group_id: UUID, concert_id: UUID, epoch: int) -> e.Concert:
        await self.groups.access(group_id, admin=True)
        concert = await self.store.get(e.Concert, concert_id)
        require(concert.group_id == group_id, "not_found")
        require(
            concert.deleted_at is not None and concert.lifecycle_version == epoch, "confirmation"
        )
        assert concert.deleted_at is not None
        require(concert.deleted_at + RETENTION > e.now(), "trash_expired")
        concert.deleted_at = None
        concert.lifecycle_version += 1
        return concert

    async def _clear_contexts(self, group_id: UUID, concert_id: UUID | None = None) -> None:
        for context in await self.store.find(e.Context, group_id=group_id):
            if concert_id is None or context.concert_id == concert_id:
                context.concert_id, context.observed_version = None, None
                if concert_id is None:
                    context.group_id, context.selected_song_id = None, None

    async def _cancel_outbox(
        self, group_id: UUID, *, concert_id: UUID | None = None, chat_id: int | None = None
    ) -> None:
        filters = {"group_id": group_id}
        if concert_id is not None:
            filters["concert_id"] = concert_id
        events = await self.store.find(e.Outbox, **filters)
        if chat_id is not None:
            events += await self.store.find(e.Outbox, chat_id=chat_id, group_id=None)
        for event in events:
            event.sent = True


class TrashCleanup:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def run(self, at: datetime | None = None) -> None:
        at = at or e.now()
        for group in await self.store.expired(e.Group, "deleted_at", at - RETENTION):
            group = await self.store.get(e.Group, group.id, lock=True)
            if group.deleted_at is not None and group.deleted_at + RETENTION <= at:
                await self._purge_group(group)
        for concert in await self.store.expired(e.Concert, "deleted_at", at - RETENTION):
            await self.store.get(e.Group, concert.group_id, lock=True)
            concert = await self.store.get(e.Concert, concert.id, lock=True)
            if concert.deleted_at is not None and concert.deleted_at + RETENTION <= at:
                await self._purge_concert(concert)
        for template in await self.store.expired(e.SongListTemplate, "expires_at", at):
            await self.store.delete(template)
        for confirmation in await self.store.expired(e.Confirmation, "expires_at", at):
            await self.store.delete(confirmation)

    async def _delete(self, kind: type[e.Entity], **filters: object) -> None:
        for row in await self.store.find(kind, **filters):
            await self.store.delete(row)
        await self.store.flush()

    async def _purge_concert(self, concert: e.Concert) -> None:
        filters = {"group_id": concert.group_id, "concert_id": concert.id}
        for kind in (e.Outbox, e.SongListTemplate, e.PersonalDraft, e.Submission):
            await self._delete(kind, **filters)
        for setlist in await self.store.find(e.Setlist, **filters):
            setlist.approved_revision_id = None
            await self.store.flush()
            await self._delete(e.Revision, setlist_id=setlist.id)
            for block in await self.store.find(e.Block, setlist_id=setlist.id):
                await self._delete(e.Item, block_id=block.id)
                await self.store.delete(block)
            await self.store.flush()
            await self.store.delete(setlist)
        for context in await self.store.find(e.Context, concert_id=concert.id):
            context.concert_id, context.observed_version = None, None
        await self.store.flush()
        await self.store.delete(concert)
        await self.store.flush()

    async def _purge_group(self, group: e.Group) -> None:
        for concert in await self.store.find(e.Concert, group_id=group.id):
            await self._purge_concert(concert)
        for kind in (
            e.Outbox,
            e.SongListTemplate,
            e.CatalogImport,
            e.CatalogProposal,
            e.SongAlias,
            e.Song,
            e.Membership,
            e.ChatBinding,
        ):
            await self._delete(kind, group_id=group.id)
        for context in await self.store.find(e.Context, group_id=group.id):
            context.group_id, context.concert_id = None, None
            context.observed_version, context.selected_song_id = None, None
        await self.store.flush()
        await self.store.delete(group)
        await self.store.flush()
