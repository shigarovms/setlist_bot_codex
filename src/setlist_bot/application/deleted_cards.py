"""RU: Завершение карточек удалённого выступления. EN: Durable markup-only updates."""

import json

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Concert, Outbox


async def retire_cards(store: Store, concert: Concert) -> None:
    targets = set()
    for row in await store.concert_cards(concert.group_id, concert.id):
        try:
            data = json.loads(row.command)
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            not isinstance(data, dict)
            or data.get("kind") != "setlist_menu"
            or data.get("group_id") != str(concert.group_id)
            or data.get("concert_id") != str(concert.id)
            or not data.get("message_ids")
        ):
            continue
        # RU/EN: Old buttons become unusable in the same transaction as the deletion.
        row.command = json.dumps(
            {**data, "closed": True, "retired_epoch": concert.lifecycle_version}, ensure_ascii=False
        )
        target = (row.chat_id, data["message_ids"][-1])
        if target in targets:
            continue
        targets.add(target)
        store.add(
            Outbox(
                group_id=concert.group_id,
                concert_id=concert.id,
                chat_id=row.chat_id,
                text="Обновление кнопок удалённого выступления",
                support_kind="retire",
                payload={"card_id": str(row.id), "concert_epoch": concert.lifecycle_version},
            )
        )
