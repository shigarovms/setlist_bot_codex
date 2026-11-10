"""Private HTML input adapter. Save through Setlists.edit_songs, never publish/approve."""

import hashlib
import json
from collections import Counter
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.recommendations import recommendation_key
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.concerts import button_date, today
from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    Membership,
    Revision,
    Setlist,
    Song,
    SongListTemplate,
    User,
    now,
)
from setlist_bot.domain.rules import flat_content, is_pause, normalize, parse_pause, require, totals


def entries(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for block in flat_content(snapshot)["blocks"]:
        for item in block["items"]:
            if is_pause(item):
                result.append(
                    {
                        "kind": "pause",
                        "duration_seconds": item["duration_seconds"],
                        **({"pause_label": item["pause_label"]} if "pause_label" in item else {}),
                    }
                )
            else:
                result.append(
                    {
                        "kind": "song",
                        "song_id": item["song_id"],
                        "version": item["song_version"],
                        "title": item["title"],
                        "duration_seconds": item["duration_seconds"],
                        "pause_before": item.get("pause_before"),
                    }
                )
    return result


class MiniDrafts:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user
        self.groups, self.lists = Groups(store, user), Setlists(store, user)

    async def create(self, group_id: UUID, concert_id: UUID) -> Confirmation:
        await self.groups.can_suggest(group_id)
        group = await self.groups.access(group_id)
        concert = await self.store.get(Concert, concert_id)
        await self.lists.load(group_id, concert_id)
        snapshot, version, _ = await self.lists.read(group_id, concert_id)
        bindings = await self.store.find(ChatBinding, group_id=group_id)
        session = Confirmation(
            user_id=self.user.id,
            chat_id=self.user.telegram_id,
            expires_at=now() + timedelta(hours=24),
            command=json.dumps(
                {
                    "kind": "miniapp",
                    "group_id": str(group_id),
                    "concert_id": str(concert_id),
                    "group_epoch": group.lifecycle_version,
                    "concert_epoch": concert.lifecycle_version,
                    "origin_chat_id": bindings[0].chat_id if bindings else None,
                    "history_max_version": version,
                    "history_empty": not entries(snapshot),
                }
            ),
        )
        self.store.add(session)
        return session

    async def template_target(self, identifier: UUID) -> SongListTemplate:
        template = await self.store.get(SongListTemplate, identifier)
        require(
            template.expires_at > now() and template.owner_id in (None, self.user.id),
            "template_expired",
        )
        await self.groups.can_suggest(template.group_id)
        group = await self.groups.access(template.group_id)
        await self.lists.load(group.id, template.concert_id)
        concert = await self.store.get(Concert, template.concert_id)
        require(
            template.group_epoch == group.lifecycle_version
            and template.concert_epoch == concert.lifecycle_version,
            "template_expired",
        )
        if template.owner_id is None:
            require(
                bool(
                    await self.store.find(ChatBinding, group_id=group.id, chat_id=template.chat_id)
                ),
                "template_expired",
            )
        return template

    async def open_template(self, identifier: UUID) -> Confirmation:
        template = await self.template_target(identifier)
        # RU: Коллектив уже заблокирован; параллельные открытия не создадут две сессии.
        # EN: Each authenticated participant gets their own owner-bound session.
        for row in await self.store.find(
            Confirmation, user_id=self.user.id, chat_id=self.user.telegram_id
        ):
            data = json.loads(row.command)
            if (
                data.get("kind") == "miniapp"
                and data.get("launch_id") == str(identifier)
                and row.expires_at > now()
            ):
                _, data, target = await self.load(row.id)
                snapshot, version, _ = await self.lists.read(template.group_id, target.id)
                # RU: Повторное открытие видит новые общие версии, личный ввод остаётся.
                # EN: Reset only the navigation anchor; preserve draft and retry receipts.
                row.command = json.dumps(
                    {
                        **{key: value for key, value in data.items() if key != "history_sources"},
                        "history_max_version": version,
                        "history_empty": not entries(snapshot),
                    }
                )
                return row
        row = await self.create(template.group_id, template.concert_id)
        row.command = json.dumps({**json.loads(row.command), "launch_id": str(identifier)})
        return row

    async def load(self, identifier: UUID) -> tuple[Confirmation, dict[str, Any], Concert]:
        row = await self.store.get(Confirmation, identifier)
        data = json.loads(row.command)
        require(
            row.user_id == self.user.id
            and row.chat_id == self.user.telegram_id
            and row.expires_at > now()
            and data.get("kind") == "miniapp",
            "miniapp_expired",
        )
        group_id = UUID(data["group_id"])
        await self.groups.can_suggest(group_id)
        group = await self.groups.access(group_id)
        concert = await self.store.get(Concert, UUID(data["concert_id"]))
        require(
            concert.group_id == group_id
            and concert.deleted_at is None
            and group.lifecycle_version == data["group_epoch"]
            and concert.lifecycle_version == data["concert_epoch"],
            "miniapp_expired",
        )
        await self.groups.check_performance_visibility(group_id, concert.id)
        # RU: Сначала блокируем коллектив, затем сессию, как в командах бота.
        # EN: Match the bot's lock order; the session target cannot switch with /groups.
        row = await self.store.get(Confirmation, identifier, lock=True)
        return row, json.loads(row.command), concert

    async def state(self, identifier: UUID, *, catalog: bool = True) -> dict[str, Any]:
        _, data, concert = await self.load(identifier)
        group_id = UUID(data["group_id"])
        group = await self.groups.access(group_id)
        snapshot, version, _ = await self.lists.read(group_id, concert.id, private=True)
        songs = await self.store.find(Song, group_id=group_id, archived=False) if catalog else []
        require(len(songs) <= 5000, "miniapp_catalog_limit")
        counts: Counter[str] = Counter()
        active = await self.store.visible_concert_ids(self.user.id, group_id) if catalog else set()
        revisions = {
            s.approved_revision_id
            for s in (await self.store.find(Setlist, group_id=group_id) if catalog else [])
            if s.approved_revision_id and s.concert_id in active
        }
        for revision in await self.store.get_many(Revision, revisions):
            if revision.group_id == group_id:
                counts.update(
                    item["song_id"]
                    for b in flat_content(revision.snapshot)["blocks"]
                    for item in b["items"]
                    if not is_pause(item)
                )
        songs.sort(key=lambda song: (-counts[str(song.id)], normalize(song.title), song.id))
        result = {
            "language": self.user.language,
            "group": group.name,
            "title": concert.title,
            "date": button_date(concert.date),
            "concert_id": str(concert.id),
            "version": version,
            "session": str(identifier),
            "default_pause_seconds": snapshot["default_pause_seconds"],
            "group_default_pause_seconds": group.default_pause_seconds,
            "can_set_group_default": any(
                member.role == "admin"
                for member in await self.store.find(
                    Membership, group_id=group_id, user_id=self.user.id, status="active"
                )
            ),
            "planned_seconds": concert.performance_duration_seconds,
            "metadata_version": concert.metadata_version,
            "entries": entries(snapshot),
            "catalog": [
                {
                    "id": str(song.id),
                    "title": song.title,
                    "version": song.version,
                    "duration_seconds": song.duration_seconds,
                }
                for song in songs
            ],
        }
        if not catalog:
            result.pop("catalog")
        return result

    async def _history(self, identifier: UUID) -> tuple[UUID, list[dict[str, str]]]:
        row, data, target = await self.load(identifier)
        if "history_sources" not in data:
            sources = await self._history_sources(target.group_id, target, data)
            data = {**data, "history_sources": sources}
            row.command = json.dumps(data)
        return target.group_id, data["history_sources"]

    def _history_label(self, kind: str, concert: Concert, revision: Revision) -> str:
        russian = self.user.language == "ru"
        label = " · ".join(part for part in (button_date(concert.date), concert.title) if part)
        if kind == "version":
            return ("Версия " if russian else "Version ") + str(revision.number)
        if kind == "recommendation":
            return ("Рекомендуем · " if russian else "Suggested · ") + label
        return label

    async def history_menu(self, identifier: UUID) -> list[dict[str, Any]]:
        group_id, sources = await self._history(identifier)
        revisions = {
            r.id: r
            for r in await self.store.get_many(
                Revision, {UUID(source["revision_id"]) for source in sources}
            )
            if r.group_id == group_id
        }
        lists = {
            s.id: s
            for s in await self.store.get_many(Setlist, {r.setlist_id for r in revisions.values()})
            if s.group_id == group_id
        }
        visible = await self.store.visible_concert_ids(self.user.id, group_id)
        events = {
            c.id: c
            for c in await self.store.get_many(
                Concert, {s.concert_id for s in lists.values()} & visible
            )
        }
        result = []
        for index, source in enumerate(sources):
            revision = revisions.get(UUID(source["revision_id"]))
            if revision is None or revision.setlist_id not in lists:
                continue
            event = events.get(lists[revision.setlist_id].concert_id)
            if event is not None:
                result.append(
                    {
                        "index": index,
                        "label": self._history_label(source["kind"], event, revision),
                        "seconds": totals(revision.snapshot)[2],
                    }
                )
        return result

    async def history(self, identifier: UUID, index: int) -> dict[str, Any]:
        group_id, sources = await self._history(identifier)
        require(type(index) is int and 0 <= index < len(sources), "miniapp_no_history")
        source = sources[index]
        revision = await self.store.get(Revision, UUID(source["revision_id"]))
        setlist = await self.store.get(Setlist, revision.setlist_id)
        concert = await self.store.get(Concert, setlist.concert_id)
        require(
            revision.group_id == setlist.group_id == concert.group_id == group_id
            and concert.deleted_at is None
            and concert.id in await self.store.visible_concert_ids(self.user.id, group_id),
            "miniapp_no_history",
        )
        return {
            "index": index,
            "count": len(sources),
            "kind": source["kind"],
            "label": self._history_label(source["kind"], concert, revision),
            "default_pause_seconds": flat_content(revision.snapshot)["default_pause_seconds"],
            "seconds": totals(revision.snapshot)[2],
            "entries": entries(revision.snapshot),
        }

    async def _history_sources(
        self, group_id: UUID, target: Concert, data: dict[str, Any]
    ) -> list[dict[str, str]]:
        current = await self.lists.load(group_id, target.id)
        versions = [
            r
            for r in await self.lists.history(group_id, target.id)
            if r.number <= data.get("history_max_version", current.version) and entries(r.snapshot)
        ]
        versions.sort(key=lambda r: r.number, reverse=True)
        visible = await self.store.visible_concert_ids(self.user.id, group_id)
        concerts = {
            c.id: c
            for c in await self.store.find(Concert, group_id=group_id, deleted_at=None)
            if c.id in visible and c.date < min(today(), target.date)
        }
        past = [
            s
            for s in await self.store.find(Setlist, group_id=group_id)
            if s.concert_id in concerts and s.approved_revision_id
        ]
        past.sort(
            key=lambda source: (concerts[source.concert_id].date, source.concert_id), reverse=True
        )
        approved = {
            r.setlist_id: r
            for r in await self.store.get_many(
                Revision, {s.approved_revision_id for s in past if s.approved_revision_id}
            )
        }
        past = [s for s in past if s.id in approved and entries(approved[s.id].snapshot)]
        result = [{"kind": "version", "revision_id": str(r.id)} for r in versions]
        if not versions and data.get("history_empty", True) and past:
            best = min(
                past,
                key=lambda s: recommendation_key(
                    target,
                    concerts[s.concert_id],
                    totals(approved[s.id].snapshot)[2],
                ),
            )
            result.append({"kind": "recommendation", "revision_id": str(best.approved_revision_id)})
            past.remove(best)
        result.extend(
            {"kind": "performance", "revision_id": str(s.approved_revision_id)} for s in past
        )
        return result

    async def save(
        self,
        identifier: UUID,
        expected: int,
        values: list[dict[str, Any]],
        request_id: str,
        default_pause_seconds: int | None = None,
    ) -> dict[str, Any]:
        row, data, concert = await self.load(identifier)
        require(type(expected) is int and isinstance(values, list) and len(values) <= 200, "input")
        require(all(isinstance(item, dict) for item in values), "input")
        require(isinstance(request_id, str), "input")
        require(str(UUID(request_id)) == request_id, "input")
        require(
            default_pause_seconds is None
            or (type(default_pause_seconds) is int and 0 <= default_pause_seconds <= 86400),
            "pause_duration",
        )
        require(
            all(
                item.get("kind") != "song"
                or isinstance(item.get("song_id"), str)
                and type(item.get("version")) is int
                for item in values
            ),
            "input",
        )
        # RU: Старое открытое окно повторяет прежний запрос без поля средней.
        # EN: Preserve legacy retry fingerprints across a rolling deployment.
        fingerprint_values = [expected, values]
        if default_pause_seconds is not None:
            fingerprint_values.append(default_pause_seconds)
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_values, sort_keys=True).encode()
        ).hexdigest()
        if data.get("save_request") == request_id:
            require(data.get("save_fingerprint") == fingerprint, "input")
            current = await self.state(identifier, catalog=False)
            require(current["version"] == data["saved_version"], "conflict")
            return current
        group_id = UUID(data["group_id"])
        ordered: list[UUID | int] = []
        before: list[int | None] = []
        labels: list[str | None] = []
        ids = {UUID(item["song_id"]) for item in values if item.get("kind") == "song"}
        songs = {song.id: song for song in await self.store.get_many(Song, ids)}
        for item in values:
            labels.append(item.get("pause_label") if item.get("kind") == "pause" else None)
            if item.get("kind") == "pause":
                seconds = item["duration_seconds"]
                require(type(seconds) is int and 0 < seconds <= 86400, "pause_duration")
                ordered.append(seconds)
                before.append(None)
            else:
                require(item.get("kind") == "song", "input")
                song = songs.get(UUID(item["song_id"]))
                require(
                    song is not None
                    and song.group_id == group_id
                    and not song.archived
                    and song.version == item.get("version"),
                    "selection_catalog_changed",
                )
                assert song is not None
                lead = item.get("pause_before")
                require(lead is None or type(lead) is int and 0 <= lead <= 86400, "pause_duration")
                ordered.append(song.id)
                before.append(lead)
        previous, _, _ = await self.lists.read(group_id, concert.id, private=True)
        await self.lists.edit_songs(
            group_id,
            concert.id,
            expected,
            [],
            replace=True,
            private=True,
            ordered=ordered,
            pauses_before=before,
            pause_labels=labels,
            default_pause_seconds=default_pause_seconds,
        )
        if (
            default_pause_seconds is not None
            and default_pause_seconds != previous["default_pause_seconds"]
        ):
            membership = (
                await self.store.find(
                    Membership, group_id=group_id, user_id=self.user.id, status="active"
                )
            )[0]
            if membership.role == "admin":
                # RU: В одной транзакции обновляем черновик и настройку новых выступлений.
                # EN: Never rewrite approved history, existing setlists or other private drafts.
                await self.groups.set_default_pause(group_id, str(default_pause_seconds))
                record_activity(
                    self.store,
                    self.user,
                    "group_default_pause_changed",
                    group_id,
                    seconds=default_pause_seconds,
                )
        result = await self.state(identifier, catalog=False)
        row.command = json.dumps(
            {
                **data,
                "save_request": request_id,
                "save_fingerprint": fingerprint,
                "saved_version": result["version"],
            }
        )
        record_activity(
            self.store, self.user, "miniapp_draft_saved", concert.id, group_id=str(group_id)
        )
        return result

    async def publication_status(self, identifier: UUID) -> dict[str, Any]:
        _, data, concert = await self.load(identifier)
        require(
            bool(data.get("publication_id")) and data.get("origin_chat_id") is not None, "input"
        )
        require(
            bool(
                await self.store.find(
                    ChatBinding, group_id=concert.group_id, chat_id=data["origin_chat_id"]
                )
            ),
            "miniapp_expired",
        )
        return {
            "version": data.get("publication_version"),
            "status": await self.store.publication_delivery(
                UUID(data["publication_id"]), concert.group_id, concert.id, data["origin_chat_id"]
            ),
        }

    async def pause(self, identifier: UUID, value: str) -> int:
        await self.load(identifier)
        require(isinstance(value, str) and len(value) <= 80, "pause_duration")
        text = value.strip()
        seconds = parse_pause(text + " мин" if text.isdecimal() else text)
        require(seconds is not None and 0 < seconds <= 86400, "pause_duration")
        assert seconds is not None
        return seconds
