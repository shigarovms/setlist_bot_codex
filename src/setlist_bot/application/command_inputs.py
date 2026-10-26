"""Owner-bound ForceReply input, replayed through the existing command handlers."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.concert_details import FIELDS as CONCERT_INPUTS
from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    Context,
    Group,
    Song,
    User,
    now,
)
from setlist_bot.domain.rules import require

KIND = "command_input"
GROUP_INPUTS = frozenset(
    {
        "song",
        "import",
        "edit",
        "edit_bpm",
        "edit_lights",
        "edit_comment",
        "copy",
        "alias",
        "concert",
        "group_language",
        "group_pause",
        "archive",
        "unarchive",
    }
)
VERSION_INPUTS = frozenset(
    {
        "add",
        "move",
        "remove",
        "pause",
        "picker_pause",
        "suggestion",
    }
)
SETLIST_INPUTS = VERSION_INPUTS - {"suggestion", "picker_pause"}
COMMANDS = GROUP_INPUTS | VERSION_INPUTS | CONCERT_INPUTS | {"language", "register"}
SELECTED_INPUTS = frozenset(
    {"edit", "copy", "alias", "archive", "unarchive", "edit_bpm", "edit_lights", "edit_comment"}
)


class CommandInputs:
    def __init__(self, store: Store, user: User, chat_id: int, private: bool) -> None:
        self.store, self.user, self.chat_id, self.private = store, user, chat_id, private

    @staticmethod
    def data(row: Confirmation) -> dict[str, Any]:
        return dict(json.loads(row.command))

    @staticmethod
    def write(row: Confirmation, data: dict[str, Any]) -> None:
        row.command = json.dumps(data, ensure_ascii=False)

    async def clear(self) -> None:
        for old in await self.store.find(Confirmation, user_id=self.user.id, chat_id=self.chat_id):
            if self.data(old).get("kind") == KIND:
                await self.store.delete(old)

    async def create(
        self,
        command: str,
        context: Context | None,
        prompt: str,
        *,
        version: int | None = None,
        monospace_body: str | None = None,
    ) -> Confirmation:
        require(command in COMMANDS, "input")
        await self.clear()
        group_id = context.group_id if context is not None else None
        concert_id = (
            context.concert_id
            if context is not None and command in (VERSION_INPUTS | CONCERT_INPUTS)
            else None
        )
        selected_id = (
            context.selected_song_id if context is not None and command in SELECTED_INPUTS else None
        )
        group = await self.store.get(Group, group_id) if group_id else None
        concert = await self.store.get(Concert, concert_id) if concert_id else None
        selected = await self.store.get(Song, selected_id) if selected_id else None
        row = Confirmation(
            user_id=self.user.id,
            chat_id=self.chat_id,
            command="",
            expires_at=now() + timedelta(minutes=10),
        )
        self.write(
            row,
            {
                "kind": KIND,
                "command": command,
                "prompt": prompt,
                "monospace_body": monospace_body,
                "prompt_id": None,
                "private": self.private,
                "group_id": str(group_id) if group_id else None,
                "concert_id": str(concert_id) if concert_id else None,
                "group_epoch": group.lifecycle_version if group else None,
                "concert_epoch": concert.lifecycle_version if concert else None,
                "version": version,
                "metadata_version": concert.metadata_version if concert else None,
                "selected_song_id": str(selected_id) if selected_id else None,
                "selected_version": selected.version if selected else None,
            },
        )
        self.store.add(row)
        return row

    async def reply(self, prompt_id: int) -> Confirmation | None:
        for row in await self.store.find(Confirmation, user_id=self.user.id, chat_id=self.chat_id):
            data = self.data(row)
            if data.get("kind") == KIND and data.get("prompt_id") == prompt_id:
                return row
        return None

    async def restore(self, row: Confirmation) -> dict[str, Any]:
        """RU: Ответ относится к исходному вопросу, а не к последнему выбранному концерту.

        Повторно проверяем владельца, чат, срок, роль и версии: кнопка/вопрос могли
        остаться в Telegram после изменения данных или удаления мероприятия.
        EN: Bind replies to their original prompt, not the latest selected concert.
        Revalidate ownership, chat, expiry, permissions and versions at execution.
        """
        data = self.data(row)
        require(
            row.user_id == self.user.id
            and row.chat_id == self.chat_id
            and row.expires_at > now()
            and data.get("kind") == KIND
            and data.get("private") == self.private
            and data.get("command") in COMMANDS,
            "input_expired",
        )
        if data["group_id"] is None:
            return data
        group_id = UUID(data["group_id"])
        if data["command"] == "register":
            group = await self.store.get(Group, group_id, lock=True)
            require(group.deleted_at is None, "in_trash")
        else:
            group = await Groups(self.store, self.user).access(group_id)
        require(group.lifecycle_version == data["group_epoch"], "input_expired")
        if not self.private:
            require(
                bool(await self.store.find(ChatBinding, group_id=group_id, chat_id=self.chat_id)),
                "input_expired",
            )
        context = await Groups(self.store, self.user).context(self.chat_id, self.private)
        if context.group_id != group_id:
            context.concert_id, context.observed_version, context.selected_song_id = (
                None,
                None,
                None,
            )
        context.group_id = group_id
        if data["concert_id"] is not None:
            concert_id = UUID(data["concert_id"])
            concert = await self.store.get(Concert, concert_id)
            require(
                concert.group_id == group_id
                and concert.deleted_at is None
                and concert.lifecycle_version == data["concert_epoch"],
                "input_expired",
            )
            context.concert_id = concert_id
            if data["command"] in CONCERT_INPUTS:
                await Groups(self.store, self.user).access(group_id, admin=True)
                require(concert.metadata_version == data["metadata_version"], "conflict")
            else:
                _, version, _ = await Setlists(self.store, self.user).read(
                    group_id, concert_id, private=self.private
                )
                require(version == data["version"], "conflict")
                context.observed_version = version
        if data["command"] in SELECTED_INPUTS:
            context.selected_song_id = (
                UUID(data["selected_song_id"]) if data["selected_song_id"] else None
            )
            if context.selected_song_id:
                song = await self.store.get(Song, context.selected_song_id)
                require(
                    song.group_id == group_id and song.version == data["selected_version"],
                    "conflict",
                )
        return data
