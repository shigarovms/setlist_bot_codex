"""Explicit publication only; background autosaves never enter this adapter."""

import hashlib
import json
from typing import Any
from uuid import UUID

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.domain.rules import require
from setlist_bot.transport.publications import queue_publication


async def publish(
    service: MiniDrafts,
    identifier: UUID,
    expected: int,
    values: list[dict[str, Any]],
    request_id: str,
) -> dict[str, Any]:
    row, data, concert = await service.load(identifier)
    require(
        type(expected) is int and isinstance(values, list) and isinstance(request_id, str), "input"
    )
    require(str(UUID(request_id)) == request_id, "input")
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps([expected, values], sort_keys=True).encode()
    ).hexdigest()
    if data.get("publication_request") == request_id:
        require(data["publication_fingerprint"] == fingerprint, "input")
        return await service.publication_status(identifier)
    if data.get("publication_version") == expected and data.get("publication_values") == digest:
        previous = await service.publication_status(identifier)
        if previous["status"] != "failed":
            return previous
    require(data.get("origin_chat_id") is not None, "miniapp_expired")
    saved = await service.save(identifier, expected, values, request_id)
    event = await queue_publication(
        service.store,
        service.user,
        concert.group_id,
        concert.id,
        saved["version"],
        origin_chat_id=data["origin_chat_id"],
    )
    # RU: Версии/идемпотентность сохраняются вместе с предложением и очередью в одной транзакции.
    # EN: Retrying a lost HTTP response cannot enqueue a second publication.
    row.command = json.dumps(
        {
            **json.loads(row.command),
            "publication_request": request_id,
            "publication_fingerprint": fingerprint,
            "publication_values": digest,
            "publication_version": saved["version"],
            "publication_id": str(event.id),
        }
    )
    return {"status": "pending", "version": saved["version"]}
