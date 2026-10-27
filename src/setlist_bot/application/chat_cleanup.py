"""Use existing dialog references only; do not create a message-history journal."""

import json
from typing import Any
from uuid import UUID

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Confirmation, SetlistMessage


def message_ids(data: dict[str, Any]) -> set[int]:
    result = {
        int(i)
        for key in ("message_ids", "panel_ids", "transient_message_ids")
        for i in data.get(key, [])
    }
    for key in ("prompt_id", "keyboard_message_id"):
        if data.get(key) is not None:
            result.add(int(data[key]))
    return result


async def dialogs(store: Store, chat_id: int) -> dict[UUID, tuple[dict[str, Any], Confirmation]]:
    return {
        row.id: (json.loads(row.command), row)
        for row in await store.find(Confirmation, chat_id=chat_id)
    }


async def collect(store: Store, chat_id: int) -> list[int]:
    identifiers = set()
    for data, row in (await dialogs(store, chat_id)).values():
        identifiers.update(message_ids(data))
        # Closing the UI must not delete its underlying setlist, revision or draft.
        if data.get("kind") == "setlist_menu":
            row.command = json.dumps({**data, "closed": True})
    for reference in await store.find(SetlistMessage, chat_id=chat_id):
        identifiers.add(reference.message_id)
    return sorted(identifiers)
