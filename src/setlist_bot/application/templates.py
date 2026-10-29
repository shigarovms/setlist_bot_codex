from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import ChatBinding, Concert, SongListTemplate, User, now
from setlist_bot.domain.rules import clock, flat_content, is_pause, require


class Templates:
    def __init__(self, store: Store, user: User, chat_id: int) -> None:
        self.store, self.user, self.chat_id = store, user, chat_id

    def create(
        self,
        group_id: UUID,
        concert_id: UUID,
        snapshot: dict[str, Any],
        *,
        private: bool,
        chat_id: int | None = None,
        version: int | None = None,
        group_epoch: int = 0,
        concert_epoch: int = 0,
    ) -> SongListTemplate:
        template = SongListTemplate(
            group_id=group_id,
            concert_id=concert_id,
            chat_id=self.chat_id if chat_id is None else chat_id,
            owner_id=self.user.id if private else None,
            titles=[
                clock(i["duration_seconds"]) if is_pause(i) else i["title"]
                for b in flat_content(snapshot)["blocks"]
                for i in b["items"]
            ],
            expires_at=now() + timedelta(days=30),
            version=version,
            group_epoch=group_epoch,
            concert_epoch=concert_epoch,
        )
        self.store.add(template)
        return template

    async def load(self, template_id: UUID) -> SongListTemplate:
        template = await self.store.get(SongListTemplate, template_id)
        require(template.chat_id == self.chat_id, "not_found")
        require(template.owner_id is None or template.owner_id == self.user.id, "not_found")
        require(template.expires_at > now(), "template_expired")
        await Groups(self.store, self.user).can_suggest(template.group_id)
        group = await Groups(self.store, self.user).access(template.group_id)
        await Setlists(self.store, self.user).load(template.group_id, template.concert_id)
        concert = await self.store.get(Concert, template.concert_id)
        require(
            template.version is not None
            and template.group_epoch == group.lifecycle_version
            and template.concert_epoch == concert.lifecycle_version,
            "template_expired",
        )
        if template.owner_id is None:
            bindings = await self.store.find(ChatBinding, chat_id=self.chat_id)
            require(bool(bindings) and bindings[0].group_id == template.group_id, "not_found")
        return template
