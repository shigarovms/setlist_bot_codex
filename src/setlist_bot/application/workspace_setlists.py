"""Shared setlist reads and explicit, versioned workspace mutations."""

import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.miniapp import entries
from setlist_bot.application.ports import Store
from setlist_bot.application.setlist_menus import REQUEST, SetlistMenus
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import (
    Concert,
    Confirmation,
    Group,
    Membership,
    Revision,
    Song,
    Submission,
    User,
    now,
)
from setlist_bot.domain.rules import normalize, pause_item, require, totals

SETLIST_SESSION = "workspace_setlist"
PAGE_SIZE = 20


def revision_token(group: Group, concert: Concert, snapshot: dict[str, Any]) -> str:
    content = [group.lifecycle_version, concert.lifecycle_version, snapshot]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


class WorkspaceSetlists:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups = Groups(store, user)
        self.lists = Setlists(store, user)

    async def open(self, group_id: UUID, concert_id: UUID) -> Confirmation:
        await self.groups.performance_access(group_id, concert_id)
        group = await self.store.get(Group, group_id)
        concert = await self.store.get(Concert, concert_id)
        require(concert.group_id == group_id and concert.deleted_at is None, "not_found")
        row = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=24),
            command=json.dumps(
                {
                    "kind": SETLIST_SESSION,
                    "group_id": str(group_id),
                    "concert_id": str(concert_id),
                    "group_epoch": group.lifecycle_version,
                    "concert_epoch": concert.lifecycle_version,
                }
            ),
        )
        self.store.add(row)
        return row

    async def editor(self, group_id: UUID, concert_id: UUID) -> dict[str, Any]:
        await self.groups.can_suggest(group_id)
        snapshot, version, approved = await self.lists.read(group_id, concert_id)
        require(not approved, "approved")
        group = await self.store.get(Group, group_id)
        concert = await self.store.get(Concert, concert_id)
        catalog = await self.store.find(Song, group_id=group_id, archived=False)
        require(len(catalog) <= 5000, "miniapp_catalog_limit")
        catalog.sort(key=lambda song: (normalize(song.title), song.id))
        return {
            "version": version,
            "token": revision_token(group, concert, snapshot),
            "default_pause_seconds": snapshot["default_pause_seconds"],
            "entries": entries(snapshot),
            "catalog": [
                {
                    "id": str(song.id),
                    "title": song.title,
                    "version": song.version,
                    "duration_seconds": song.duration_seconds,
                }
                for song in catalog
            ],
        }

    async def variants(
        self, group_id: UUID, concert_id: UUID, page: int, *, history: bool = False
    ) -> dict[str, Any]:
        setlist = await self.lists.load(group_id, concert_id)
        proposals = (
            []
            if history
            else await self.store.find(Submission, group_id=group_id, concert_id=concert_id)
        )
        proposals.sort(key=lambda row: (row.applied, row.id))
        revisions = await self.store.find(Revision, group_id=group_id, setlist_id=setlist.id)
        historical = sorted(
            (row for row in revisions if history or row.suggestion_number is not None),
            key=lambda row: row.number,
            reverse=True,
        )
        rows: list[tuple[str, Submission | Revision]] = [
            *(("submission", row) for row in proposals),
            *(("revision", row) for row in historical),
        ]
        start = page * PAGE_SIZE
        visible = rows[start : start + PAGE_SIZE]
        names = {
            user_id: await self.groups.name(group_id, user_id)
            for user_id in {row.author_id for _, row in visible}
        }
        return {
            "has_next": start + PAGE_SIZE < len(rows),
            "variants": [
                {
                    "id": str(row.id),
                    "kind": kind,
                    "author": names[row.author_id],
                    "seconds": totals(row.snapshot)[2],
                    "applied": row.applied if isinstance(row, Submission) else False,
                    "stale": isinstance(row, Submission)
                    and row.snapshot.get("shared_version") not in (None, setlist.version),
                    "number": row.number if isinstance(row, Revision) else None,
                    "action": row.action if isinstance(row, Revision) else None,
                    "created_at": row.created_at.isoformat() if isinstance(row, Revision) else None,
                }
                for kind, row in visible
            ],
        }

    async def variant(
        self, group_id: UUID, concert_id: UUID, kind: str, identifier: UUID
    ) -> dict[str, Any]:
        setlist = await self.lists.load(group_id, concert_id)
        if kind == "submission":
            submission = await self.store.get(Submission, identifier)
            require(
                submission.group_id == group_id and submission.concert_id == concert_id, "not_found"
            )
            snapshot = submission.snapshot
        else:
            require(kind == "revision", "input")
            revision = await self.store.get(Revision, identifier)
            require(
                revision.group_id == group_id and revision.setlist_id == setlist.id,
                "not_found",
            )
            snapshot = revision.snapshot
        return {"entries": entries(snapshot)}

    async def requests(self, group_id: UUID, concert_id: UUID) -> dict[str, Any]:
        group = await self.groups.access(group_id, admin=True)
        snapshot, version, approved = await self.lists.read(group_id, concert_id)
        concert = await self.store.get(Concert, concert_id)
        requests = []
        for row in await self.store.find(
            Confirmation, user_id=self.user.id, chat_id=self.user.telegram_id
        ):
            item = SetlistMenus.data(row)
            if (
                row.expires_at > now()
                and item.get("kind") == REQUEST
                and item.get("status") == "pending"
                and item["group_id"] == str(group_id)
                and item["concert_id"] == str(concert_id)
                and item["group_epoch"] == group.lifecycle_version
                and item["concert_epoch"] == concert.lifecycle_version
                and item["version"] == version
                and item["approved"] == approved
                and (approved or item["snapshot"] == snapshot)
            ):
                requests.append(
                    {
                        "id": str(row.id),
                        "operation": item["operation"],
                        "requester": await self.groups.name(group_id, UUID(item["requester_id"])),
                    }
                )
        return {"requests": requests}

    @staticmethod
    def command(payload: dict[str, Any]) -> dict[str, Any]:
        action, version, token = (payload.get(key) for key in ("action", "version", "token"))
        require(action in ("edit", "apply", "restore", "approve", "reopen", "decide"), "input")
        require(
            type(version) is int and version >= 0 and isinstance(token, str) and len(token) == 64,
            "input",
        )
        data = {"action": action, "version": version, "token": token}
        if action in ("apply", "restore"):
            data["variant"] = str(UUID(payload["variant"]))
        if action == "decide":
            require(type(payload.get("accept")) is bool, "input")
            data.update(request=str(UUID(payload["request"])), accept=payload["accept"])
        if action == "edit":
            values, pause = payload.get("entries"), payload.get("default_pause_seconds")
            require(
                isinstance(values, list)
                and len(values) <= 200
                and all(isinstance(row, dict) for row in values),
                "input",
            )
            require(type(pause) is int and 0 <= pause <= 86400, "pause_duration")
            data.update(entries=values, default_pause_seconds=pause)
        return data

    async def apply(self, group_id: UUID, concert_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
        snapshot, version, approved = await self.lists.read(group_id, concert_id)
        group = await self.store.get(Group, group_id)
        concert = await self.store.get(Concert, concert_id)
        require(
            version == data["version"]
            and revision_token(group, concert, snapshot) == data["token"],
            "conflict",
        )
        action = data["action"]
        if action == "decide":
            await self.groups.access(group_id, admin=True)
            identifier = UUID(data["request"])
            row = await self.store.get(Confirmation, identifier)
            item = SetlistMenus.data(row)
            require(
                item.get("group_id") == str(group_id) and item.get("concert_id") == str(concert_id),
                "not_found",
            )
            menus = SetlistMenus(self.store, self.user, self.user.telegram_id)
            await menus.decide(identifier, data["accept"])
            if data["accept"]:
                await queue_card_refreshes(self.store, group_id, concert_id)
            return {"requested": False, "decision": data["accept"], "resolved": str(identifier)}
        if action in ("approve", "reopen"):
            require(approved == (action == "reopen"), "conflict")
            membership = (
                await self.store.find(
                    Membership, group_id=group_id, user_id=self.user.id, status="active"
                )
            )[0]
            if membership.role != "admin":
                menus = SetlistMenus(self.store, self.user, self.user.telegram_id)
                request, _, created = await menus.request(
                    None,
                    {
                        "group_id": str(group_id),
                        "concert_id": str(concert_id),
                        "group_epoch": group.lifecycle_version,
                        "concert_epoch": concert.lifecycle_version,
                        "personal": False,
                        "private_chat": True,
                        "version": version,
                        "approved": approved,
                        "snapshot": snapshot,
                    },
                    action,
                )
                return {
                    "requested": True,
                    "duplicate": not created,
                    "notify": str(request.id) if created else None,
                }
            operation = self.lists.approve if action == "approve" else self.lists.reopen
            await operation(group_id, concert_id, version)
        elif action == "edit":
            await self._edit(group_id, concert_id, version, data)
        elif action == "apply":
            proposal = await self.store.get(Submission, UUID(data["variant"]))
            require(
                proposal.group_id == group_id and proposal.concert_id == concert_id, "not_found"
            )
            require(proposal.snapshot.get("shared_version") in (None, version), "conflict")
            await self.lists.apply(group_id, concert_id, proposal.id, version)
        else:
            identifier = UUID(data["variant"])
            await self.variant(group_id, concert_id, "revision", identifier)
            await self.lists.restore(group_id, concert_id, identifier, version)
        await queue_card_refreshes(self.store, group_id, concert_id)
        return {"requested": False}

    async def _edit(
        self, group_id: UUID, concert_id: UUID, version: int, data: dict[str, Any]
    ) -> None:
        await self.groups.can_suggest(group_id)
        ordered: list[UUID | int] = []
        labels: list[str | None] = []
        leads: list[int | None] = []
        for row in data["entries"]:
            if row.get("kind") == "pause":
                seconds, label = row.get("duration_seconds"), row.get("pause_label")
                require(type(seconds) is int and 0 < seconds <= 86400, "pause_duration")
                pause_item(seconds, label)
                ordered.append(seconds)
                labels.append(label)
                leads.append(None)
            else:
                require(
                    row.get("kind") == "song"
                    and isinstance(row.get("song_id"), str)
                    and type(row.get("version")) is int,
                    "input",
                )
                song = await self.store.get(Song, UUID(row["song_id"]))
                require(
                    song.group_id == group_id
                    and not song.archived
                    and song.version == row["version"],
                    "selection_catalog_changed",
                )
                ordered.append(song.id)
                labels.append(None)
                leads.append(row.get("pause_before"))
        await self.lists.edit_songs(
            group_id,
            concert_id,
            version,
            [],
            replace=True,
            ordered=ordered,
            pauses_before=leads,
            pause_labels=labels,
            default_pause_seconds=data["default_pause_seconds"],
        )
