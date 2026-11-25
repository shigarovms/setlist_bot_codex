"""Telegram messages and actions for the event timetable."""

from typing import Any
from uuid import UUID

from setlist_bot.application.workspace_timing import (
    TIMING_KIND,
    WorkspaceTiming,
    responsible,
)
from setlist_bot.domain.concerts import concert_label
from setlist_bot.domain.entities import Concert, Group, Outbox
from setlist_bot.domain.rules import require
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.i18n import tr


def timing_response(
    group: Group, event: Concert, message: Outbox, language: str, base_url: str
) -> Response:
    data = message.payload
    warning = data["kind"] == "warning"
    lines = [group.name, concert_label(event, language), tr(language, "timing_title")]
    if data["preliminary"]:
        lines.append(tr(language, "timing_preliminary"))
    lines.append(data["zone"])
    rows = data["rows"]
    if not rows:
        lines.append(tr(language, "timing_empty"))
    for row in rows:
        interval = f"{row['start']}–{row['end']}" if row["end"] else row["start"]
        day = " (+1)" if row["next_day"] else ""
        lines.append(f"{interval}{day} · {row['label']}")
    if warning:
        lines.append(tr(language, "timing_warning"))
    response = Response("\n".join(lines))
    if base_url:
        key = "timing_open"
        response.web_apps[key] = (
            base_url.rstrip("/") + f"/miniapp/?workspace=1&view=timing&band={group.id}"
            f"&performance={event.id}&from=notification"
        )
        response.buttons.append((tr(language, "timing_edit" if warning else "timing_title"), key))
    if warning and rows:
        response.buttons.append((tr(language, "timing_notify"), f"pt_send {message.id.hex}"))
        if not data["preliminary"]:
            response.buttons.append((tr(language, "timing_mark"), f"pt_pre {message.id.hex}"))
    return response


async def handle(c: Controller, action: str, identifier: UUID) -> Response:
    require(c.private, "private_only")
    message = await c.store.get(Outbox, identifier)
    require(
        message.support_kind == TIMING_KIND
        and message.chat_id == c.user.telegram_id
        and message.payload.get("kind") == "warning"
        and message.concert_id is not None
        and message.group_id is not None,
        "input_expired",
    )
    assert message.concert_id is not None and message.group_id is not None
    service = WorkspaceTiming(c.store, c.user)
    group, event, _, _ = await service.scope(message.concert_id, message.group_id)
    data = message.payload
    require(
        await responsible(c.store, group, event) == c.user.id
        and event.date.isoformat() == data["date"],
        "input_expired",
    )
    command: dict[str, Any] = {
        "performance": str(event.id),
        "epoch": data["epoch"],
        "version": data["version"],
    }
    if action == "pt_pre":
        result = await service.apply(
            group.id, {**command, "action": "save", "rows": data["rows"], "preliminary": True}
        )
        return Response(c.t("timing_marked"))
    message = await c.store.get(Outbox, identifier, lock=True)
    if "manual_count" in message.payload:
        return Response(c.t("timing_queued", count=message.payload["manual_count"]))
    result = await service.apply(group.id, {**command, "action": "notify"})
    message.payload = {**message.payload, "manual_count": result["queued"]}
    return Response(c.t("timing_queued", count=result["queued"]))
