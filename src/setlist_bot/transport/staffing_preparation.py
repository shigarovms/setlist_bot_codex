"""Pre-invitation edits in existing Confirmation sessions, never in a second roster table.

RU: «Назад» отбрасывает только временные правки. EN: Save is atomic and version-bound;
invites remain a separate, authorized action after availability review.
"""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.staffing import Staffing
from setlist_bot.application.user_lookup import known_user
from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    GigInvitation,
    GigSlot,
    LineupTemplate,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.rules import require
from setlist_bot.domain.staff_input import role_person
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.response_context import staffing_context
from setlist_bot.transport.staffing import action, prompt


def clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def write(row: Confirmation, data: dict[str, Any]) -> None:
    data["version"] += 1
    row.command = json.dumps(data, ensure_ascii=False)


def button(
    c: Controller, row: Confirmation, data: dict[str, Any], operation: str, **extra: Any
) -> str:
    return action(
        c,
        "prep_" + operation,
        group_id=data["group_id"],
        event_id=data["event_id"],
        preview_id=str(row.id),
        preview_version=data["version"],
        owner=str(c.user.id),
        **extra,
    )


async def source(c: Controller, event: Concert) -> list[dict[str, Any]]:
    slots = sorted(
        await c.store.find(GigSlot, concert_id=event.id, removed=False), key=lambda s: s.position
    )
    return [
        {
            "role_id": str(slot.role_id),
            "user_id": str(slot.preferred_user_id) if slot.preferred_user_id else None,
        }
        for slot in slots
    ]


@staffing_context
async def start_preparation(c: Controller, event_id: UUID) -> Response:
    event = await Staffing(c.store, c.user).event(event_id)
    data = {
        "kind": "staff_preparation",
        "group_id": str(event.group_id),
        "event_id": str(event.id),
        "epoch": event.lifecycle_version,
        "event_version": event.lineup_version,
        "version": 0,
        "stage": "review",
        "name": event.lineup_name,
        "values": await source(c, event),
    }
    data["base"] = clone(data["values"])
    if not data["values"]:
        data["stage"] = "choose"
    row = Confirmation(
        user_id=c.user.id,
        chat_id=c.chat_id,
        expires_at=now() + timedelta(days=1),
        command=json.dumps(data),
    )
    c.store.add(row)
    return await review(c, row, data) if data["values"] else await choices(c, row, data)


async def load(c: Controller, args: dict[str, Any]) -> tuple[Confirmation, dict[str, Any], Concert]:
    row = await c.store.get(Confirmation, UUID(args["preview_id"]))
    data = json.loads(row.command)
    require(
        data.get("kind") == "staff_preparation"
        and row.user_id == c.user.id
        and row.chat_id == c.chat_id
        and row.expires_at > now()
        and data["version"] == args["preview_version"],
        "input_expired",
    )
    event = await Staffing(c.store, c.user).event(UUID(data["event_id"]))
    require(
        str(event.group_id) == data["group_id"]
        and event.lifecycle_version == data["epoch"]
        and event.lineup_version == data["event_version"],
        "input_expired",
    )
    if not c.private:
        require(
            bool(await c.store.find(ChatBinding, group_id=event.group_id, chat_id=c.chat_id)),
            "forbidden",
        )
    require(
        not any(
            row.status in {"pending", "confirmed", "conditional", "accepted", "reconfirm"}
            for row in await c.store.find(GigInvitation, concert_id=event.id)
        ),
        "input_expired",
    )
    return row, data, event


async def labels(c: Controller, values: list[dict[str, Any]]) -> list[str]:
    roles = {
        r.id: r
        for r in await c.store.get_many(
            StaffRole, {UUID(v["role_id"]) for v in values if v.get("role_id")}
        )
    }
    people = {
        p.id: p
        for p in await c.store.get_many(
            User, {UUID(v["user_id"]) for v in values if v.get("user_id")}
        )
    }
    from setlist_bot.transport.staffing_lineups import person_name

    return [
        (roles[UUID(v["role_id"])].name if v.get("role_id") else v["role_name"])
        + (" — " + person_name(people[UUID(v["user_id"])]) if v.get("user_id") else "")
        for v in values
    ]


async def review(c: Controller, row: Confirmation, data: dict[str, Any]) -> Response:
    text = c.t("staff_creation_check") + "\n\n" + "\n".join(await labels(c, data["values"]))
    return Response(
        text,
        [
            (c.t("staff_check_send"), button(c, row, data, "invite")),
            (c.t("staff_edit_preparation"), button(c, row, data, "edit")),
            (c.t("staff_to_lineups"), button(c, row, data, "choose")),
        ],
    )


async def edit_view(
    c: Controller, row: Confirmation, data: dict[str, Any], page: int = 0
) -> Response:
    require(page >= 0, "input")
    lines = await labels(c, data["values"])
    buttons = [
        (
            f"{i + 1}. {label}: " + c.t("staff_invite_or_remove"),
            button(c, row, data, "slot", index=i),
        )
        for i, label in enumerate(lines)
        if page * 8 <= i < (page + 1) * 8
    ]
    for delta, symbol in ((-1, "◀"), (1, "▶")):
        if page + delta >= 0 and (delta < 0 or (page + 1) * 8 < len(lines)):
            buttons.append((symbol, button(c, row, data, "page", page=page + delta)))
    buttons.extend(
        [
            (c.t("staff_add_participant"), button(c, row, data, "add")),
            (c.t("staff_confirm_preparation"), button(c, row, data, "save")),
            (c.t("menu_back"), button(c, row, data, "leave")),
        ]
    )
    text = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines)) or c.t(
        "staff_empty_lineup"
    )
    return Response(text, buttons)


async def choices(
    c: Controller, row: Confirmation, data: dict[str, Any], page: int = 0
) -> Response:
    require(page >= 0, "input")
    rows = sorted(
        await c.store.find(LineupTemplate, group_id=UUID(data["group_id"])),
        key=lambda t: (t.created_at, t.id),
    )
    buttons = [
        (
            template.name,
            button(
                c,
                row,
                data,
                "template",
                template_id=str(template.id),
                template_version=template.version,
            ),
        )
        for template in rows[page * 15 : (page + 1) * 15]
    ]
    for delta, symbol in ((-1, "◀"), (1, "▶")):
        if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(rows)):
            buttons.append((symbol, button(c, row, data, "choose_page", page=page + delta)))
    buttons.append((c.t("staff_choose_later"), button(c, row, data, "later")))
    return Response(c.t("staff_creation_choose"), buttons)


async def preparation_input(c: Controller, args: dict[str, Any], value: str) -> Response | None:
    if args["operation"] not in ("prep_add", "prep_person"):
        return None
    row, data, _ = await load(c, args)
    require(data["stage"] == "edit", "input_expired")
    if args["operation"] == "prep_add":
        require(len(data["values"]) < 40, "input")
        name, reference = role_person(value)
        person = await known_user(c.store, reference) if reference else None
        data["values"].append({"role_name": name, "user_id": str(person.id) if person else None})
    else:
        index = args["index"]
        require(0 <= index < len(data["values"]), "input_expired")
        person = await known_user(c.store, value.strip()) if value.strip() != "-" else None
        data["values"][index]["user_id"] = str(person.id) if person else None
    write(row, data)
    return await edit_view(c, row, data)


@staffing_context
async def preparation_action(c: Controller, op: str, args: dict[str, Any]) -> Response | None:
    if not op.startswith("prep_"):
        return None
    row, data, event = await load(c, args)
    operation = op.removeprefix("prep_")
    service = Staffing(c.store, c.user)
    if operation == "edit":
        require(data["stage"] == "review", "input_expired")
        data["values"] = clone(data["base"])
        data["stage"] = "edit"
        write(row, data)
        return await edit_view(c, row, data)
    if operation == "leave":
        require(data["stage"] == "edit", "input_expired")
        if data["values"] == data["base"]:
            data["stage"] = "review"
            write(row, data)
            return await review(c, row, data)
        return Response(
            c.t("staff_unsaved_preparation"),
            [
                (c.t("staff_confirm_preparation"), button(c, row, data, "save")),
                (c.t("staff_discard_preparation"), button(c, row, data, "discard")),
                (c.t("staff_edit_preparation"), button(c, row, data, "page", page=0)),
            ],
        )
    if operation in ("review", "discard"):
        if operation == "discard":
            require(data["stage"] == "edit", "input_expired")
            data["values"] = clone(data["base"])
            data["stage"] = "review"
            write(row, data)
        require(data["stage"] == "review", "input_expired")
        return await review(c, row, data)
    if operation in ("choose", "choose_page"):
        data["stage"] = "choose"
        write(row, data)
        return await choices(c, row, data, int(args.get("page", 0)))
    if operation in ("template", "later"):
        require(data["stage"] == "choose", "input_expired")
        values = []
        name = ""
        if operation == "template":
            template = await c.store.get(LineupTemplate, UUID(args["template_id"]))
            require(
                template.group_id == event.group_id
                and template.version == args["template_version"],
                "input_expired",
            )
            values = clone(template.slots)
            name = template.name
        await service.replace_uninvited_lineup(event.id, data["event_version"], name, values)
        if operation == "later":
            await c.store.delete(row)
            return c._concert_settings(event)
        data.update(
            values=await source(c, event),
            name=name,
            event_version=event.lineup_version,
            stage="review",
        )
        data["base"] = clone(data["values"])
        write(row, data)
        return await review(c, row, data)
    if operation == "invite":
        require(data["stage"] == "review", "input_expired")
        if not any(v.get("user_id") for v in data["values"]):
            return Response(
                c.t("staff_no_candidates"),
                [
                    (c.t("staff_edit_preparation"), button(c, row, data, "edit")),
                    (c.t("menu_back"), button(c, row, data, "review")),
                ],
            )
        from setlist_bot.transport.staffing import handle

        callback = action(
            c,
            "defaults_preview",
            event_id=str(event.id),
            group_id=str(event.group_id),
            back=button(c, row, data, "review"),
            preview_id=str(row.id),
            preview_version=data["version"],
        )
        return await handle(c, *callback.split(" ", 1))
    require(data["stage"] == "edit", "input_expired")
    if operation == "page":
        return await edit_view(c, row, data, int(args["page"]))
    if operation == "save":
        await service.replace_uninvited_lineup(
            event.id, data["event_version"], data["name"], data["values"]
        )
        data.update(
            values=await source(c, event), event_version=event.lineup_version, stage="review"
        )
        data["base"] = clone(data["values"])
        write(row, data)
        return await review(c, row, data)
    if operation == "add":
        return await prompt(
            c,
            "prep_add",
            group_id=event.group_id,
            preview_id=str(row.id),
            preview_version=data["version"],
        )
    index = args["index"]
    require(0 <= index < len(data["values"]), "input_expired")
    item = data["values"][index]
    if operation == "remove":
        data["values"].pop(index)
        write(row, data)
        return await edit_view(c, row, data)
    if operation == "pick":
        person = await c.store.get(User, UUID(args["person_id"]))
        role_id = UUID(item["role_id"]) if item.get("role_id") else None
        require(role_id is not None, "input_expired")
        assert role_id is not None
        require(
            any(
                candidate.id == person.id
                for _, candidate, _, _ in await Staffing(c.store, c.user).role_candidates(
                    event, role_id
                )
            ),
            "input_expired",
        )
        item["user_id"] = str(person.id)
        write(row, data)
        return await edit_view(c, row, data)
    if operation == "manual":
        return await prompt(
            c,
            "prep_person",
            group_id=event.group_id,
            preview_id=str(row.id),
            preview_version=data["version"],
            index=index,
        )
    require(operation == "slot", "input")
    candidates = (
        await Staffing(c.store, c.user).role_candidates(event, UUID(item["role_id"]))
        if item.get("role_id")
        else []
    )
    contacts = [contact for contact, _, _, _ in candidates]
    people = {person.id: person for _, person, _, _ in candidates}
    from setlist_bot.transport.staffing_lineups import person_name

    page = int(args.get("page", 0))
    require(page >= 0, "input")
    buttons = [
        (
            person_name(people[contact.user_id]),
            button(c, row, data, "pick", index=index, person_id=str(contact.user_id)),
        )
        for contact in contacts[page * 15 : (page + 1) * 15]
    ]
    for delta, symbol in ((-1, "◀"), (1, "▶")):
        if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(contacts)):
            buttons.append((symbol, button(c, row, data, "slot", index=index, page=page + delta)))
    buttons.extend(
        [
            (c.t("staff_enter_person"), button(c, row, data, "manual", index=index)),
            (c.t("staff_remove_event_role"), button(c, row, data, "remove", index=index)),
            (c.t("menu_back"), button(c, row, data, "page", page=index // 8)),
        ]
    )
    return Response((await labels(c, [item]))[0], buttons)
