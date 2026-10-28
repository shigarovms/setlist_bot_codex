"""RU: Переписка поддержки и события. EN: Durable support routing and audit notices."""

from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import (
    Outbox,
    SupportCase,
    SupportMessage,
    SupportSettings,
    User,
    now,
)
from setlist_bot.domain.rules import require


class Support:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def configure(self, chat_id: int, username: str) -> None:
        # RU: Сериализуем первую привязку; username берём только из Telegram Update.
        # EN: Serialize bootstrap; never accept an owner name from message text.
        await self.store.lock_chat(0)
        rows = await self.store.find(SupportSettings, key="main")
        require(
            self.user.telegram_id == rows[0].owner_telegram_id
            if rows
            else username.casefold() == "shigarovms",
            "forbidden",
        )
        require(chat_id < 0, "group_only")
        if rows:
            rows[0].chat_id = chat_id
        else:
            self.store.add(
                SupportSettings(owner_telegram_id=self.user.telegram_id, chat_id=chat_id)
            )

    async def notify(self, text: str) -> None:
        rows = await self.store.find(SupportSettings, key="main")
        if rows:
            self.store.add(Outbox(chat_id=rows[0].chat_id, text=text, support_kind="event"))

    async def submit(self, text: str) -> bool:
        rows = await self.store.find(SupportSettings, key="main")
        if not rows:
            return False
        event = Outbox(
            support_kind="request",
            chat_id=rows[0].chat_id,
            text=(
                f"Обращение в поддержку / Support request\n{self.actor}\n\n{text}"
                "\n\nОтветьте на это сообщение через Reply."
            ),
        )
        self.store.add(event)
        await self.store.flush()
        self.store.add(
            SupportMessage(outbox_id=event.id, user_id=self.user.id, chat_id=event.chat_id)
        )
        return True

    @property
    def actor(self) -> str:
        return f"{self.user.display_name or 'Пользователь'} (Telegram ID: {self.user.telegram_id})"

    async def reply(self, chat_id: int, message_id: int, text: str) -> bool:
        settings = await self.store.find(SupportSettings, key="main", chat_id=chat_id)
        if not settings:
            return False
        rows = await self.store.find(SupportMessage, chat_id=chat_id, message_id=message_id)
        if not rows:
            return False
        await self.reply_to_request(rows[0].outbox_id, text)
        return True

    async def case(self, request_id: UUID, expected: int | None = None) -> SupportCase:
        request = await self.store.get(Outbox, request_id, lock=True)
        require(request.support_kind == "request", "not_found")
        require(bool(await self.store.find(SupportMessage, outbox_id=request_id)), "not_found")
        cases = await self.store.find(SupportCase, request_id=request_id)
        case = cases[0] if cases else SupportCase(request_id=request_id)
        if not cases:
            self.store.add(case)
        require(expected is None or expected == case.version, "conflict")
        return case

    async def reply_to_request(
        self,
        request_id: UUID,
        text: str,
        *,
        expected_version: int | None = None,
        source: str = "telegram",
        actor: str | None = None,
    ) -> Outbox:
        """RU/EN: Отвечаем только на реальное обращение / never send to an arbitrary ID."""
        require(0 < len(text.strip()) <= 4000, "input")
        case = await self.case(request_id, expected_version)
        routes = await self.store.find(SupportMessage, outbox_id=request_id)
        require(bool(routes), "not_found")
        user = await self.store.get(User, routes[0].user_id)
        event = Outbox(
            actor=actor or f"telegram:{self.user.telegram_id}",
            source=source,
            chat_id=user.telegram_id,
            support_kind="reply",
            support_request_id=request_id,
            text=f"Ответ поддержки / Support reply\n\n{text.strip()}",
        )
        self.store.add(event)
        case.version += 1
        case.updated_at = now()
        case.last_reply_by, case.last_reply_source = event.actor, source
        record_activity(
            self.store, self.user, "support_reply", request_id, reply_outbox_id=str(event.id)
        )
        return event
