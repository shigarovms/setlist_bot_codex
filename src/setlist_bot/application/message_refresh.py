"""Queue updates of existing shared cards, without opening personal drafts."""

import json
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import ChatBinding, Confirmation, Outbox, now


async def queue_card_refreshes(
    store: Store, group_id: UUID, concert_id: UUID | None = None
) -> None:
    # RU: Импорт 1000 песен должен поставить одно обновление каждой карточки.
    # EN: Coalesce per-card jobs under the caller's tenant lock, including within a batch.
    queued = {
        row.payload.get("card_id")
        for row in await store.find(Outbox, group_id=group_id, support_kind="refresh", sent=False)
    }
    # RU: Читаем только привязанные чаты коллектива, не все сессии всех пользователей.
    # EN: Scoped by chat; private cards and historical closed cards are never refreshed.
    for binding in await store.find(ChatBinding, group_id=group_id):
        for card in await store.find(Confirmation, chat_id=binding.chat_id):
            data = json.loads(card.command)
            if (
                card.expires_at <= now()
                or data.get("kind") != "setlist_menu"
                or data.get("closed")
                or data.get("personal")
                or data.get("private_chat")
                or data.get("owner_id") is not None
                or not data.get("message_ids")
                or data.get("group_id") != str(group_id)
                or str(card.id) in queued
                or (concert_id is not None and data.get("concert_id") != str(concert_id))
            ):
                continue
            store.add(
                Outbox(
                    group_id=group_id,
                    concert_id=UUID(data["concert_id"]),
                    chat_id=card.chat_id,
                    text="Обновление карточки сет-листа",
                    support_kind="refresh",
                    payload={"card_id": str(card.id)},
                )
            )
