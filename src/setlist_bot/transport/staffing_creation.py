"""Attach a saved lineup to the existing performance preview/confirmation workflow."""

import json
from typing import Any
from uuid import UUID

from setlist_bot.domain.cities import city_name
from setlist_bot.domain.concerts import confirmation_date, parse_concert
from setlist_bot.domain.display import short_duration
from setlist_bot.domain.entities import LineupTemplate
from setlist_bot.domain.performance_input import (
    SEPARATOR,
    TYPE_ALIASES,
    parse_performance,
    performance_seconds,
)
from setlist_bot.domain.rules import normalize, require
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.staffing import action


async def preview(
    c: Controller, group_id: UUID, payload: dict[str, Any], lineup: LineupTemplate | None
) -> Response:
    await c.groups.access(group_id, admin=True)
    from datetime import date

    text = c.t(
        "concert_create_preview",
        date=confirmation_date(date.fromisoformat(payload["date"]), c.language),
        title=payload["title"],
    )
    for label, value in (
        (
            "concert_duration_label",
            short_duration(payload["duration"], c.language, minutes_only=True)
            if payload["duration"]
            else "",
        ),
        ("concert_type_label", payload["performance_type"]),
        ("concert_city_label", payload["city"]),
        ("concert_info_label", payload["info"]),
    ):
        if value:
            text += "\n" + c.t(label) + ": " + str(value)
    values = dict(payload)
    if lineup:
        require(lineup.group_id == group_id, "forbidden")
        values["_lineup_id"], values["_lineup_version"] = str(lineup.id), lineup.version
        text += "\n" + c.t("staff_creation_selected", name=lineup.name)
    else:
        text += "\n" + c.t("staff_no_lineup")
    context = await c.groups.context(c.chat_id, c.private)
    require(context.group_id == group_id, "input_expired")
    response = await c.confirm(
        context, "create_performance " + json.dumps(values, ensure_ascii=False), text
    )
    key = response.buttons[0][1].split()[1]
    response.buttons.insert(1, (c.t("concert_correct"), "concert_correct " + key))
    return response


async def prepare(c: Controller, group_id: UUID, value: str) -> Response:
    await c.groups.access(group_id, admin=True)
    lineups = sorted(
        await c.store.find(LineupTemplate, group_id=group_id),
        key=lambda row: (row.created_at, row.id),
    )
    when, remainder = parse_concert(value)
    selected = None
    kept = []
    # RU: Состав распознаётся как отдельное поле, как город и тип выступления.
    # EN: Match whole fields only; a substring in a venue name must not pick a lineup.
    for field in SEPARATOR.split(remainder):
        key = normalize(field)
        explicit = key.startswith("состав:") or key.startswith("lineup:")
        if explicit:
            key = normalize(field.split(":", 1)[1])
        reserved = bool(
            city_name(field)
            or performance_seconds(field)
            or any(
                key == normalize(alias) for aliases in TYPE_ALIASES.values() for alias in aliases
            )
        )
        matches = [
            row
            for row in lineups
            if (explicit or not reserved)
            and key in (row.name_key, row.name_key + " состав", "состав " + row.name_key)
        ]
        require(not explicit or bool(matches), "input")
        if matches:
            require(selected is None, "input")
            selected = matches[0]
        else:
            kept.append(field)
    payload = parse_performance(when.isoformat() + " " + "  ".join(kept)).payload()
    if selected or len(lineups) == 1:
        return await preview(c, group_id, payload, selected or lineups[0])
    if not lineups:
        response = await preview(c, group_id, payload, None)
        response.buttons.insert(
            1,
            (
                c.t("staff_setup_main"),
                action(
                    c,
                    "prompt",
                    field="template",
                    group_id=str(group_id),
                    main=True,
                    resume_creation=value,
                    owner=str(c.user.id),
                ),
            ),
        )
        return response
    return await choices(c, group_id, payload)


async def choices(
    c: Controller, group_id: UUID, payload: dict[str, Any], page: int = 0
) -> Response:
    await c.groups.access(group_id, admin=True)
    require(page >= 0, "input")
    lineups = sorted(
        await c.store.find(LineupTemplate, group_id=group_id),
        key=lambda row: (row.created_at, row.id),
    )
    buttons = [
        (
            row.name,
            action(
                c,
                "creation_lineup",
                group_id=str(group_id),
                template_id=str(row.id),
                version=row.version,
                payload=payload,
                owner=str(c.user.id),
            ),
        )
        for row in lineups[page * 15 : (page + 1) * 15]
    ]

    for delta, symbol in ((-1, "◀"), (1, "▶")):
        if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(lineups)):
            buttons.append(
                (
                    symbol,
                    action(
                        c,
                        "creation_page",
                        group_id=str(group_id),
                        payload=payload,
                        page=page + delta,
                        owner=str(c.user.id),
                    ),
                )
            )
    buttons.append(
        (
            c.t("staff_without_lineup"),
            action(
                c, "creation_lineup", group_id=str(group_id), payload=payload, owner=str(c.user.id)
            ),
        )
    )
    return Response(c.t("staff_creation_choose"), buttons)


async def choose(c: Controller, data: dict[str, Any]) -> Response:
    group_id = UUID(data["group_id"])
    await c.groups.access(group_id, admin=True)
    lineup = None
    if data.get("template_id"):
        lineup = await c.store.get(LineupTemplate, UUID(data["template_id"]))
        require(lineup.group_id == group_id and lineup.version == data["version"], "input_expired")
    return await preview(c, group_id, data["payload"], lineup)
