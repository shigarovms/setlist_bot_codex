"""Telegram lineup menus. All writes reuse Staffing and the current transaction.

RU: Кнопки несут версии, а не права доступа; права повторно проверяет сервис.
EN: Stored callback state is not authorization; application methods recheck access.
"""

from typing import Any
from uuid import UUID

from setlist_bot.application.staffing import OPEN, Staffing
from setlist_bot.application.user_lookup import known_user
from setlist_bot.domain.entities import (
    GigInvitation,
    GigSlot,
    Group,
    LineupTemplate,
    Membership,
    StaffContact,
    StaffRole,
    User,
)
from setlist_bot.domain.rules import normalize, require
from setlist_bot.domain.staff_input import role_person
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.staffing import action, prompt


def person_name(person: User) -> str:
    return (
        "@" + person.username if person.username else person.display_name or str(person.telegram_id)
    )


async def event_lineup(c: Controller, identifier: UUID, page: int = 0) -> Response:
    service = Staffing(c.store, c.user)
    event = await service.event(identifier, admin=False)
    # RU: event() уже проверил членство и заблокировал коллектив. Повторно читаем
    # только данные для экрана; каждое действие всё равно проверяет права в сервисе.
    # EN: Authorization has completed under the tenant lock; never cache permissions.
    group = await c.store.get(Group, event.group_id)
    members = await c.store.find(Membership, group_id=group.id, status="active")
    admin = any(member.user_id == c.user.id and member.role == "admin" for member in members)
    slots = sorted(
        await c.store.find(GigSlot, concert_id=event.id, removed=False), key=lambda s: s.position
    )
    require(page >= 0, "input")
    buttons = []
    invites = [
        r
        for r in await c.store.find(GigInvitation, concert_id=event.id)
        if r.status in OPEN | {"confirmed"} and service.valid(r, event, group)
    ]
    people = {p.id: p for p in await c.store.get_many(User, {r.user_id for r in invites})}
    roles = {r.id: r for r in await c.store.get_many(StaffRole, {s.role_id for s in slots})}
    names = {member.user_id: member.musical_role for member in members if member.musical_role}
    # RU: Один проход по приглашениям вместо полного перебора для каждой роли.
    # EN: Group once, then inspect only the invitations belonging to each slot.
    by_slot: dict[UUID, list[GigInvitation]] = {}
    for invitation in invites:
        by_slot.setdefault(invitation.slot_id, []).append(invitation)
    for slot_invites in by_slot.values():
        slot_invites.sort(
            key=lambda invitation: (
                invitation.status != "confirmed",
                invitation.created_at,
                invitation.id,
            )
        )
    lines = []
    for number, slot in enumerate(slots, 1):
        active = by_slot.get(slot.id, [])
        person = people[active[0].user_id] if active else None
        confirmed = bool(active and active[0].status == "confirmed")
        line = f"{number}. {roles[slot.role_id].name}"
        if confirmed and person:
            line += " — " + names.get(person.id, person.display_name or str(person.telegram_id))
            line = "✅ " + line
        lines.append(line)
        if admin and page * 8 <= number - 1 < (page + 1) * 8:
            label = (
                c.t("staff_change_person", name=person_name(person)[:48])
                if confirmed and person
                else c.t("staff_wait_person", name=person_name(person)[:48])
                if person
                else c.t("staff_invite_or_remove")
            )
            label = (
                f"{'✅ ' if confirmed else ''}{number}. {roles[slot.role_id].name[:64]}: {label}"
            )
            buttons.append((label, action(c, "slot", slot_id=str(slot.id), version=slot.version)))
    if page:
        buttons.append(("◀", f"sf_lineup {event.id.hex} {page - 1}"))
    if (page + 1) * 8 < len(slots):
        buttons.append(("▶", f"sf_lineup {event.id.hex} {page + 1}"))
    if admin:
        buttons.append(
            (
                c.t("staff_add_participant"),
                action(
                    c, "prompt", field="event_role", group_id=str(group.id), event_id=str(event.id)
                ),
            )
        )
        if any(s.preferred_user_id and s.id not in by_slot for s in slots):
            buttons.append(
                (
                    c.t("staff_check_send"),
                    action(c, "defaults_preview", event_id=str(event.id), group_id=str(group.id)),
                )
            )
        buttons.append(
            (
                c.t("staff_group_lineups", name=group.name),
                action(c, "templates", group_id=str(group.id), event_id=str(event.id)),
            )
        )
    buttons.append((c.t("menu_back"), f"use_concert {event.id}"))
    return Response("\n".join(lines) or c.t("staff_empty_lineup"), buttons)


async def template_view(
    c: Controller, row: LineupTemplate, event_id: str | None = None, page: int = 0
) -> Response:
    await c.groups.access(row.group_id, admin=True)
    buttons = []
    text = row.name
    require(page >= 0, "input")
    roles = {role.id: role for role in await c.store.find(StaffRole, group_id=row.group_id)}
    defaults = Staffing.select_defaults(
        roles,
        await c.store.find(StaffContact, group_id=row.group_id),
        [UUID(slot["role_id"]) for slot in row.slots],
        await Staffing(c.store, c.user).vocabulary(),
    )
    people = {
        person.id: person
        for person in await c.store.get_many(User, {person for person in defaults if person})
    }
    # RU: Полный текст состава не зависит от страницы; переключаются лишь кнопки.
    # EN: Batch-load the full lineup and paginate only the role actions.
    for index, slot in enumerate(row.slots):
        role = roles[UUID(slot["role_id"])]
        label = f"{index + 1}. {role.name[:64]}"
        default_person = defaults[index]
        if default_person is not None:
            label += ": " + person_name(people[default_person])[:48]
        text += "\n" + label
        if page * 8 <= index < (page + 1) * 8:
            buttons.append(
                (
                    label,
                    action(
                        c,
                        "template_role",
                        group_id=str(row.group_id),
                        template_id=str(row.id),
                        version=row.version,
                        index=index,
                    ),
                )
            )
    for delta, symbol in ((-1, "◀"), (1, "▶")):
        if page + delta >= 0 and (delta < 0 or (page + 1) * 8 < len(row.slots)):
            buttons.append(
                (
                    symbol,
                    action(
                        c,
                        "template_preview",
                        group_id=str(row.group_id),
                        template_id=str(row.id),
                        version=row.version,
                        event_id=event_id,
                        page=page + delta,
                    ),
                )
            )
    if event_id:
        event = await Staffing(c.store, c.user).event(UUID(event_id))
        require(event.group_id == row.group_id, "forbidden")
        buttons.insert(
            0,
            (
                c.t("staff_apply"),
                action(
                    c,
                    "apply_preview",
                    group_id=str(row.group_id),
                    event_id=event_id,
                    template_id=str(row.id),
                    version=row.version,
                    event_version=event.lineup_version,
                ),
            ),
        )
    for label, op, extra in (
        ("staff_add_role", "prompt", {"field": "template_add"}),
        ("staff_rename", "prompt", {"field": "template_rename"}),
        ("staff_copy", "prompt", {"field": "template_copy"}),
        ("staff_delete_lineup", "template_delete_preview", {}),
    ):
        buttons.append(
            (
                c.t(label),
                action(
                    c,
                    op,
                    group_id=str(row.group_id),
                    template_id=str(row.id),
                    version=row.version,
                    **extra,
                ),
            )
        )
    buttons.append(
        (c.t("menu_back"), action(c, "templates", group_id=str(row.group_id), event_id=event_id))
    )
    return Response(text, buttons)


async def lineup_input(c: Controller, data: dict[str, Any], value: str) -> Response | None:
    op = data["operation"]
    if op not in (
        "event_role",
        "invite_person",
        "template_add",
        "template_person",
        "template_role_name",
        "template_rename",
        "template_copy",
    ):
        return None
    service = Staffing(c.store, c.user)
    person: User | None
    group_id = UUID(data["group_id"])
    if op == "invite_person":
        person = await known_user(c.store, value.strip())
        slot = await c.store.get(GigSlot, UUID(data["slot_id"]))
        require(
            slot.group_id == group_id and not slot.removed and slot.version == data["version"],
            "input_expired",
        )
        await service.contact(group_id, slot.role_id, person.id)
        return Response(
            c.t("staff_choose_invite", name=person_name(person)[:48]),
            [
                (
                    c.t("staff_invite_person"),
                    action(
                        c,
                        "invite",
                        slot_id=str(slot.id),
                        user_id=str(person.id),
                        version=slot.version,
                        replacement_id=data.get("replacement_id"),
                    ),
                ),
                (c.t("menu_back"), action(c, "slot", slot_id=str(slot.id), version=slot.version)),
            ],
        )
    if op == "event_role":
        name, reference = role_person(value)
        person = await known_user(c.store, reference) if reference else None
        role = await service.role(group_id, name)
        slot = await service.add_slot(UUID(data["event_id"]), role.id)
        slot.preferred_user_id = person.id if person else None
        if person:
            await service.contact(group_id, role.id, person.id)
        return await event_lineup(c, slot.concert_id)
    row = await c.store.get(LineupTemplate, UUID(data["template_id"]))
    require(row.group_id == group_id and row.version == data["version"], "input_expired")
    if op == "template_rename":
        row = await service.edit_template(row.id, row.version, name=value)
    elif op == "template_copy":
        require(
            not await c.store.find(LineupTemplate, group_id=group_id, name_key=normalize(value)),
            "input",
        )
        row = await service.template(group_id, value, [dict(s) for s in row.slots])
    else:
        values = [dict(s) for s in row.slots]
        if op == "template_add":
            name, reference = role_person(value)
            person = await known_user(c.store, reference) if reference else None
            role = await service.role(group_id, name)
            values.append({"role_id": str(role.id), "user_id": str(person.id) if person else None})
        else:
            index = data["index"]
            require(0 <= index < len(values), "input_expired")
            if op == "template_role_name":
                role = await service.role(group_id, value)
                values[index]["role_id"] = str(role.id)
            else:
                person = await known_user(c.store, value.strip()) if value.strip() != "-" else None
                values[index]["user_id"] = str(person.id) if person else None
        row = await service.edit_template(row.id, row.version, values=values)
    return await template_view(c, row)


async def lineup_action(c: Controller, op: str, data: dict[str, Any]) -> Response | None:
    service = Staffing(c.store, c.user)
    if op == "onboard_later":
        from setlist_bot.transport.music_search import methods

        await c.groups.access(UUID(data["group_id"]), admin=True)
        return Response(c.t("music_onboarding"), methods(c, group_id=UUID(data["group_id"])))
    if op == "onboard_lineup":
        return await prompt(c, "template", group_id=UUID(data["group_id"]), main=True)
    if op == "templates":
        group = await c.groups.access(UUID(data["group_id"]), admin=True)
        rows = sorted(
            await c.store.find(LineupTemplate, group_id=group.id),
            key=lambda r: (r.created_at, r.id),
        )
        page = int(data.get("page", 0))
        require(page >= 0, "input")
        buttons = [
            (
                row.name,
                action(
                    c,
                    "template_preview",
                    group_id=str(group.id),
                    template_id=str(row.id),
                    version=row.version,
                    event_id=data.get("event_id"),
                ),
            )
            for row in rows[page * 15 : (page + 1) * 15]
        ]
        for delta, symbol in ((-1, "◀"), (1, "▶")):
            if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(rows)):
                buttons.append(
                    (
                        symbol,
                        action(
                            c,
                            "templates",
                            group_id=str(group.id),
                            event_id=data.get("event_id"),
                            page=page + delta,
                        ),
                    )
                )
        buttons.append(
            (
                c.t("staff_add_template"),
                action(c, "prompt", field="template", group_id=str(group.id)),
            )
        )
        buttons.append(
            (
                c.t("menu_back"),
                f"sf_lineup {UUID(data['event_id']).hex}"
                if data.get("event_id")
                else action(c, "team", group_id=str(group.id)),
            )
        )
        return Response(c.t("staff_group_lineups", name=group.name), buttons)
    if op in (
        "template_preview",
        "template_role",
        "template_remove_role",
        "template_delete_preview",
        "template_delete",
        "apply_preview",
        "apply",
    ):
        row = await c.store.get(LineupTemplate, UUID(data["template_id"]))
        await c.groups.access(row.group_id, admin=True)
        require(
            row.version == data["version"] and str(row.group_id) == data["group_id"],
            "input_expired",
        )
        if op == "template_preview":
            return await template_view(c, row, data.get("event_id"), int(data.get("page", 0)))
        if op in ("template_role", "template_remove_role"):
            index = data["index"]
            require(0 <= index < len(row.slots), "input_expired")
            if op == "template_remove_role":
                row = await service.edit_template(
                    row.id, row.version, values=[s for i, s in enumerate(row.slots) if i != index]
                )
                return await template_view(c, row)
            return Response(
                c.t("staff_role_options"),
                [
                    (
                        c.t("staff_edit_role_name"),
                        action(
                            c,
                            "prompt",
                            field="template_role_name",
                            group_id=str(row.group_id),
                            template_id=str(row.id),
                            version=row.version,
                            index=index,
                        ),
                    ),
                    (
                        c.t("permanent_title"),
                        action(c, "permanent", group_id=str(row.group_id)),
                    ),
                    (
                        c.t("staff_remove_template_role"),
                        action(
                            c,
                            "template_remove_role",
                            group_id=str(row.group_id),
                            template_id=str(row.id),
                            version=row.version,
                            index=index,
                        ),
                    ),
                    (
                        c.t("menu_back"),
                        action(
                            c,
                            "template_preview",
                            group_id=str(row.group_id),
                            template_id=str(row.id),
                            version=row.version,
                        ),
                    ),
                ],
            )
        if op == "template_delete_preview":
            return Response(
                c.t("staff_delete_check"),
                [
                    (
                        c.t("confirm_button"),
                        action(
                            c,
                            "template_delete",
                            group_id=str(row.group_id),
                            template_id=str(row.id),
                            version=row.version,
                        ),
                    ),
                    (
                        c.t("cancel_button"),
                        action(
                            c,
                            "template_preview",
                            group_id=str(row.group_id),
                            template_id=str(row.id),
                            version=row.version,
                        ),
                    ),
                ],
            )
        if op == "template_delete":
            await service.edit_template(row.id, row.version, delete=True)
            return await lineup_action(c, "templates", {"group_id": str(row.group_id)})
        event = await service.event(UUID(data["event_id"]))
        require(
            event.group_id == row.group_id and event.lineup_version == data["event_version"],
            "input_expired",
        )
        if op == "apply_preview":
            return Response(
                c.t("staff_replace_check"),
                [
                    (
                        c.t("confirm_button"),
                        action(
                            c,
                            "apply",
                            **{
                                k: v
                                for k, v in data.items()
                                if k not in ("kind", "operation", "owner")
                            },
                        ),
                    ),
                    (c.t("cancel_button"), f"sf_lineup {event.id.hex}"),
                ],
            )
        await service.apply_template(
            event.id, row.id, row.version, replace=True, event_version=event.lineup_version
        )
        return await event_lineup(c, event.id)
    if op in ("slot", "slot_remove_preview", "slot_remove", "slot_remind", "slot_other"):
        slot = await c.store.get(GigSlot, UUID(data["slot_id"]))
        if op == "slot_other":
            existing = await c.store.find(GigInvitation, slot_id=slot.id, status="confirmed")
            await c.store.lock_people({r.user_id for r in existing})
        event = await service.event(slot.concert_id)
        require(not slot.removed and slot.version == data["version"], "input_expired")
        group = await service.groups.access(event.group_id)
        active = sorted(
            [
                r
                for r in await c.store.find(GigInvitation, slot_id=slot.id)
                if r.status in OPEN | {"confirmed"} and service.valid(r, event, group)
            ],
            key=lambda r: (r.status != "confirmed", r.created_at, r.id),
        )
        if op in ("slot_remove", "slot_other", "slot_remind"):
            require(event.lineup_version == data["event_version"], "input_expired")
        if op == "slot_remove_preview":
            return Response(
                c.t("staff_remove_check"),
                [
                    (
                        c.t("confirm_button"),
                        action(
                            c,
                            "slot_remove",
                            slot_id=str(slot.id),
                            version=slot.version,
                            event_version=event.lineup_version,
                        ),
                    ),
                    (
                        c.t("cancel_button"),
                        action(c, "slot", slot_id=str(slot.id), version=slot.version),
                    ),
                ],
            )
        if op == "slot_remove":
            await service.remove_slot(slot.id, slot.version)
            return await event_lineup(c, event.id)
        if op == "slot_remind":
            require(bool(active), "input_expired")
            await service.remind(active[0].id, active[0].version)
            return Response(
                c.t("staff_reminder_queued"), [(c.t("menu_back"), f"sf_lineup {event.id.hex}")]
            )
        if op == "slot_other":
            replacement = None
            if active and active[0].status == "confirmed":
                replacement = await service.request_replacement(active[0].id, by_admin=True)
            else:
                for invite in active:
                    await service.cancel_invitation(invite.id, invite.version)
            from setlist_bot.transport.staffing import handle

            callback = action(
                c,
                "candidates",
                slot_id=str(slot.id),
                replacement_id=str(replacement.id) if replacement else None,
            )
            return await handle(c, *callback.split(" ", 1))
        role = await c.store.get(StaffRole, slot.role_id)
        buttons = []
        if active:
            first = active[0]
            if first.status in ("pending", "reconfirm", "conditional"):
                buttons.append(
                    (
                        c.t("staff_remind"),
                        action(
                            c,
                            "slot_remind",
                            slot_id=str(slot.id),
                            version=slot.version,
                            event_version=event.lineup_version,
                        ),
                    )
                )
            if len(active) > 3:
                buttons.append(
                    (c.t("staff_all_invites"), action(c, "roster", slot_id=str(slot.id)))
                )
            for invite in active[:3]:
                buttons.append(
                    (
                        c.t("staff_details"),
                        f"sf_view {invite.id.hex}"
                        if c.private
                        else f"https://t.me/{c.bot_username}?start=gig_{invite.id.hex}",
                    )
                )
        buttons.append(
            (
                c.t("staff_invite_other") if active else c.t("staff_candidates"),
                action(
                    c,
                    "slot_other",
                    slot_id=str(slot.id),
                    version=slot.version,
                    event_version=event.lineup_version,
                ),
            )
        )
        buttons.append(
            (
                c.t("staff_remove_event_role"),
                action(c, "slot_remove_preview", slot_id=str(slot.id), version=slot.version),
            )
        )
        buttons.append((c.t("menu_back"), f"sf_lineup {event.id.hex}"))
        return Response(role.name + "\n\n" + c.t("staff_slot_explanation"), buttons)
    return None
