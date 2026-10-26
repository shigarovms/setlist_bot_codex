"""RU: Переписка поддержки и события. EN: Durable support routing and audit notices."""

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Outbox, SupportMessage, SupportSettings, User
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
        user = await self.store.get(User, rows[0].user_id)
        self.store.add(
            Outbox(
                chat_id=user.telegram_id,
                support_kind="reply",
                text=f"Ответ поддержки / Support reply\n\n{text}",
            )
        )
        return True
