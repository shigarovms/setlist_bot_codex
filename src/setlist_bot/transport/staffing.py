"""Small Telegram adapter; staffing permissions and transitions live in the application."""

import asyncio
import json
import re
from datetime import timedelta
from typing import Any
from uuid import UUID

from setlist_bot.application.staffing import OPEN, Staffing
from setlist_bot.application.user_lookup import known_user
from setlist_bot.domain.concerts import button_date, parse_concert, today
from setlist_bot.domain.entities import (
    BusyPeriod,
    ChatBinding,
    Concert,
    Confirmation,
    GigInvitation,
    GigSlot,
    Group,
    LineupTemplate,
    Membership,
    Revision,
    Setlist,
    StaffContact,
    StaffReplacement,
    StaffRole,
    User,
    now,
)
from setlist_bot.domain.rules import AppError, require
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.render import render_setlist


def action(c: Controller, operation: str, **data: Any) -> str:
    row = Confirmation(
        user_id=c.user.id,
        chat_id=c.chat_id,
        expires_at=now() + timedelta(days=1),
        command=json.dumps(
            {
                "kind": "staff_action",
                "operation": operation,
                "owner": str(c.user.id) if c.private else None,
                **data,
            }
        ),
    )
    c.store.add(row)
    return f"sf_go {row.id.hex}"


async def context(c: Controller) -> Any:
    result = await c.groups.context(c.chat_id, c.private)
    require(result.group_id is not None, "select_group")
    return result


async def prompt(
    c: Controller, operation: str, *, group_id: UUID | None = None, event_id: UUID | None = None
) -> Response:
    if group_id:
        await c.groups.access(group_id, admin=True)
    row = Confirmation(
        user_id=c.user.id,
        chat_id=c.chat_id,
        expires_at=now() + timedelta(minutes=10),
        command=json.dumps(
            {
                "kind": "staff_input",
                "operation": operation,
                "group_id": str(group_id) if group_id else None,
                "event_id": str(event_id) if event_id else None,
            }
        ),
    )
    c.store.add(row)
    return Response(c.t("staff_" + operation + "_prompt"), force_reply=True, workflow_id=row.id)


async def input_reply(c: Controller, row: Confirmation, value: str) -> Response:
    data = json.loads(row.command)
    require(
        row.user_id == c.user.id and row.chat_id == c.chat_id and row.expires_at > now(),
        "input_expired",
    )
    service = Staffing(c.store, c.user)
    person: User | None
    group_id = UUID(data["group_id"]) if data.get("group_id") else None
    operation = data["operation"]
    if operation == "busy":
        require(c.private, "private_only")
        response = await busy_input(c, value)
    else:
        assert group_id is not None
        await c.groups.access(group_id, admin=True)
        if not c.private:
            require(
                bool(await c.store.find(ChatBinding, group_id=group_id, chat_id=c.chat_id)),
                "input_expired",
            )
        if operation == "role":
            role = await service.role(group_id, value)
            if data.get("event_id"):
                await service.add_slot(UUID(data["event_id"]), role.id)
                response = await lineup_view(c, UUID(data["event_id"]))
            else:
                response = await team_view(c, group_id)
        elif operation == "contact":
            fields = re.split(r"\t+| {2,}", value.strip())
            require(len(fields) == 2, "input")
            person = await known_user(c.store, fields[1])
            role = await service.role(group_id, fields[0])
            await service.contact(group_id, role.id, person.id)
            response = await contacts_view(c, group_id)
        else:
            require(operation == "template", "input")
            lines = [s.strip() for s in value.strip().splitlines() if s.strip()]
            require(2 <= len(lines) <= 41, "input")
            slots = []
            for line in lines[1:]:
                fields = re.split(r"\t+| {2,}", line)
                require(len(fields) in (1, 2), "input")
                role = await service.role(group_id, fields[0])
                person = await known_user(c.store, fields[1]) if len(fields) == 2 else None
                slots.append(
                    {"role_id": str(role.id), "user_id": str(person.id) if person else None}
                )
            await service.template(group_id, lines[0], slots)
            response = await team_view(c, group_id)
    await c.store.delete(row)
    return response


async def team_view(c: Controller, group_id: UUID) -> Response:
    group = await c.groups.access(group_id, admin=True)
    return Response(
        group.name + "\n\n" + c.t("staff_team"),
        [
            (
                c.t("staff_add_template"),
                action(c, "prompt", field="template", group_id=str(group_id)),
            ),
            (c.t("staff_templates"), action(c, "templates", group_id=str(group_id))),
            (c.t("staff_contacts"), action(c, "contacts", group_id=str(group_id))),
            (
                c.t("staff_add_contact"),
                action(c, "prompt", field="contact", group_id=str(group_id)),
            ),
            (c.t("staff_add_role"), action(c, "prompt", field="role", group_id=str(group_id))),
        ],
    )


async def contacts_view(c: Controller, group_id: UUID, page: int = 0) -> Response:
    group = await c.groups.access(group_id, admin=True)
    rows = await c.store.find(StaffContact, group_id=group_id)
    require(page >= 0, "input")
    rows.sort(key=lambda r: (r.role_id, r.user_id))
    buttons = []
    for contact in rows[page * 15 : (page + 1) * 15]:
        person = await c.store.get(User, contact.user_id)
        role = await c.store.get(StaffRole, contact.role_id)
        label = (
            "staff_hidden_label"
            if contact.preference == "hidden"
            else "staff_preferred"
            if contact.preference == "preferred"
            else "staff_normal"
        )
        buttons.append(
            (
                f"{role.name} · {person.display_name or person.telegram_id} · {c.t(label)}",
                action(c, "contact", contact_id=str(contact.id)),
            )
        )
    if page:
        buttons.append(("◀", action(c, "contacts", group_id=str(group_id), page=page - 1)))
    if (page + 1) * 15 < len(rows):
        buttons.append(("▶", action(c, "contacts", group_id=str(group_id), page=page + 1)))
    buttons += [
        (c.t("staff_add_contact"), action(c, "prompt", field="contact", group_id=str(group_id))),
        (c.t("menu_back"), action(c, "team", group_id=str(group_id))),
    ]
    return Response(group.name + "\n\n" + c.t("staff_contacts"), buttons)


async def lineup_view(c: Controller, event_id: UUID, page: int = 0) -> Response:
    service = Staffing(c.store, c.user)
    event = await service.event(event_id, admin=False)
    group = await c.store.get(Group, event.group_id)
    admin = bool(
        await c.store.find(
            Membership, group_id=group.id, user_id=c.user.id, role="admin", status="active"
        )
    )
    slots = sorted(await c.store.find(GigSlot, concert_id=event.id), key=lambda s: s.position)
    require(page >= 0, "input")
    text = f"{group.name} · {button_date(event.date)} · {event.title}\n\n{c.t('staff_lineup')}"
    buttons = []
    # RU: Большой состав и много кандидатов не должны превысить лимиты Telegram.
    # EN: Bound both the message body and keyboard; keep all invitations reachable.
    page_slots = slots[page * 8 : (page + 1) * 8]
    page_ids = {slot.id for slot in page_slots}
    roles = {r.id: r for r in await c.store.get_many(StaffRole, {s.role_id for s in page_slots})}
    all_invites = await c.store.find(GigInvitation, concert_id=event.id)
    current = [
        r
        for r in all_invites
        if r.slot_id in page_ids
        and r.status in OPEN | {"confirmed"}
        and service.valid(r, event, group)
    ]
    people = {p.id: p for p in await c.store.get_many(User, {r.user_id for r in current})}
    for slot in page_slots:
        role = roles[slot.role_id]
        invites = sorted(
            (r for r in current if r.slot_id == slot.id),
            key=lambda r: (r.status != "confirmed", r.created_at, r.id),
        )
        parts = []
        for row in invites[:3]:
            person = people[row.user_id]
            status = (
                "staff_conditional_status" if row.status == "conditional" else "staff_" + row.status
            )
            name = (person.display_name or str(person.telegram_id))[:48]
            parts.append(f"{name} · {c.t(status)}")
            if admin:
                buttons.append(
                    (
                        f"{role.name[:64]}: {name}",
                        f"sf_view {row.id.hex}",
                    )
                )
        if len(invites) > 3:
            parts.append(c.t("staff_more", count=len(invites) - 3))
            if admin:
                buttons.append(
                    (
                        c.t("staff_all_invites") + f" · {role.name[:64]}",
                        action(c, "roster", slot_id=str(slot.id)),
                    )
                )
        text += f"\n{slot.position}. {role.name[:64]} — " + (
            "; ".join(parts) if parts else c.t("staff_vacant" if slot.required else "staff_skipped")
        )
        if admin:
            buttons.append(
                (
                    f"{slot.position}. {role.name} · {c.t('staff_candidates')}",
                    action(c, "candidates", slot_id=str(slot.id)),
                )
            )
            buttons.append(
                (
                    c.t("staff_not_needed" if slot.required else "staff_needed")
                    + f" · {role.name}",
                    action(
                        c,
                        "needed",
                        slot_id=str(slot.id),
                        version=slot.version,
                        value=not slot.required,
                    ),
                )
            )
    if page:
        buttons.append(("◀", f"sf_lineup {event.id.hex} {page - 1}"))
    if (page + 1) * 8 < len(slots):
        buttons.append(("▶", f"sf_lineup {event.id.hex} {page + 1}"))
    if admin:
        defaults = [s for s in slots if s.required and s.preferred_user_id]
        if defaults:
            buttons.insert(
                0,
                (
                    c.t("staff_invite_defaults"),
                    action(c, "defaults_preview", event_id=str(event.id), group_id=str(group.id)),
                ),
            )
        if not slots:
            buttons.append(
                (
                    c.t("staff_templates"),
                    action(c, "templates", group_id=str(group.id), event_id=str(event.id)),
                )
            )
        buttons += [
            (
                c.t("staff_add_role"),
                action(c, "prompt", field="role", group_id=str(group.id), event_id=str(event.id)),
            ),
            (c.t("staff_team"), action(c, "team", group_id=str(group.id))),
        ]
    buttons.append((c.t("menu_back"), f"use_concert {event.id}"))
    return Response(text, buttons)


async def invitation_view(c: Controller, identifier: UUID) -> Response:
    require(c.private, "private_only")
    row = await c.store.get(GigInvitation, identifier)
    event = await c.store.get(Concert, row.concert_id)
    group = await c.store.get(Group, row.group_id)
    service = Staffing(c.store, c.user)
    require(service.valid(row, event, group), "input_expired")
    own = row.user_id == c.user.id
    if not own:
        await service.event(event.id)
    admin = bool(
        await c.store.find(
            Membership, group_id=group.id, user_id=c.user.id, role="admin", status="active"
        )
    )
    replacing = (
        row.replacement_id is not None
        and (await c.store.get(StaffReplacement, row.replacement_id)).status == "pending"
    )
    role = await c.store.get(StaffRole, (await c.store.get(GigSlot, row.slot_id)).role_id)
    person = await c.store.get(User, row.user_id)
    status_key = (
        "staff_conditional_status" if row.status == "conditional" else "staff_" + row.status
    )
    text = (
        f"{group.name} · {button_date(event.date)}\n{event.title} · {event.city}\n"
        f"{role.name} · {person.display_name or person.telegram_id}\n{c.t(status_key)}"
    )
    if event.extra_info:
        text += "\n\n" + event.extra_info
    buttons = []
    if own:
        if row.status == "accepted":
            buttons.append(
                (
                    c.t("staff_decline"),
                    action(
                        c, "respond", invitation_id=str(row.id), version=row.version, answer="no"
                    ),
                )
            )
        if row.status in ("pending", "reconfirm", "conditional"):
            for label, answer in (
                ("staff_confirm", "yes"),
                ("staff_decline", "no"),
                ("staff_conditional", "conditional"),
            ):
                if replacing and answer == "conditional":
                    continue
                buttons.append(
                    (
                        c.t(label),
                        action(
                            c,
                            "respond",
                            invitation_id=str(row.id),
                            version=row.version,
                            answer=answer,
                        ),
                    )
                )
        if row.status == "confirmed":
            buttons += [
                (c.t("staff_request"), action(c, "request", source_id=str(row.id))),
                (c.t("staff_guest_list"), f"sf_guest {row.id.hex}"),
                (c.t("staff_guest_pdf"), f"sf_pdf {row.id.hex}"),
            ]
            for request in await c.store.find(StaffReplacement, source_id=row.id, status="pending"):
                buttons.append(
                    (
                        c.t("staff_cancel_request"),
                        action(
                            c,
                            "cancel_request",
                            replacement_id=str(request.id),
                            version=request.version,
                        ),
                    )
                )
        if row.status == "conditional":
            text += "\n\n" + c.t("staff_waiting")
            for source in await c.store.find(
                GigInvitation, user_id=c.user.id, date=row.date, status="confirmed"
            ):
                if source.concert_id != row.concert_id:
                    buttons.append(
                        (
                            c.t("staff_request") + f" · {button_date(source.date)}",
                            action(c, "request", source_id=str(source.id), target_id=str(row.id)),
                        )
                    )
        buttons.append((c.t("menu_back"), "my_gigs"))
    if admin:
        if row.status in OPEN | {"confirmed"}:
            buttons.append(
                (
                    c.t("staff_cancel_invite"),
                    action(c, "cancel_preview", invitation_id=str(row.id), version=row.version),
                )
            )
        if row.status == "pending":
            buttons.append(
                (
                    c.t("staff_retry"),
                    action(c, "retry", invitation_id=str(row.id), version=row.version),
                )
            )
        if row.status == "accepted" and row.replacement_id:
            buttons.append(
                (
                    c.t("staff_approve_swap"),
                    action(c, "swap", invitation_id=str(row.id), version=row.version),
                )
            )
        if event.date < today() and row.status == "confirmed" and not row.worked:
            buttons.append(
                (
                    c.t("staff_worked"),
                    action(c, "worked", invitation_id=str(row.id), version=row.version),
                )
            )
        for request in await c.store.find(StaffReplacement, source_id=row.id, status="pending"):
            buttons.append(
                (
                    c.t("staff_find"),
                    action(
                        c, "candidates", slot_id=str(row.slot_id), replacement_id=str(request.id)
                    ),
                )
            )
            buttons.append(
                (
                    c.t("staff_cancel_request"),
                    action(
                        c, "cancel_request", replacement_id=str(request.id), version=request.version
                    ),
                )
            )
        buttons.append((c.t("menu_back"), f"sf_lineup {event.id.hex}"))
    return Response(text, buttons)


async def busy_input(c: Controller, value: str) -> Response:
    require(c.private, "private_only")
    parts = re.split(r"\s+[—–-]\s+", value.strip())
    require(1 <= len(parts) <= 2, "input")
    dates = []
    for part in parts:
        when, suffix = parse_concert(part)
        require(not suffix, "concert_input")
        dates.append(when)
    await Staffing(c.store, c.user).busy(dates[0], dates[-1])
    return await busy_view(c)


async def busy_view(c: Controller) -> Response:
    rows = sorted(await c.store.find(BusyPeriod, user_id=c.user.id), key=lambda r: r.starts_on)
    return Response(
        c.t("staff_busy_menu")
        + "\n\n"
        + "\n".join(f"{button_date(r.starts_on)} — {button_date(r.ends_on)}" for r in rows[-40:]),
        [(c.t("staff_busy_add"), action(c, "prompt", field="busy"))]
        + [
            (
                c.t("staff_busy_remove") + f" · {button_date(r.starts_on)}",
                action(c, "unbusy", period_id=str(r.id)),
            )
            for r in rows[-40:]
        ]
        + [(c.t("menu_back"), "my_gigs")],
    )


async def handle(c: Controller, command: str, args: str) -> Response:
    row: Any
    rows: list[Any]
    service = Staffing(c.store, c.user)
    if (
        command in ("my_gigs", "busy", "sf_view", "sf_guest", "sf_pdf", "sf_pdf_m", "sf_pdf_l")
        and not c.private
    ):
        return Response(
            c.t("staff_private"),
            [
                (
                    c.t("staff_my"),
                    f"https://t.me/{c.bot_username}?start="
                    + ("gig_" + UUID(args).hex if command.startswith("sf_") else "my_gigs"),
                )
            ],
        )
    if command == "team":
        return await team_view(c, (await context(c)).group_id)
    if command in ("lineup", "sf_lineup"):
        fields = args.split()
        identifier = UUID(fields[0]) if fields else (await context(c)).concert_id
        require(identifier is not None, "select_concert")
        if not c.private:
            event = await c.store.get(Concert, identifier)
            require(
                bool(await c.store.find(ChatBinding, group_id=event.group_id, chat_id=c.chat_id)),
                "forbidden",
            )
        return await lineup_view(c, identifier, int(fields[1]) if len(fields) > 1 else 0)
    if command == "sf_view":
        return await invitation_view(c, UUID(args))
    if command == "busy":
        return await prompt(c, "busy") if not args else await busy_input(c, args)
    if command == "my_gigs":
        page = int(args or "0")
        require(page >= 0, "input")
        rows = await c.store.find(GigInvitation, user_id=c.user.id)
        events = {e.id: e for e in await c.store.get_many(Concert, {r.concert_id for r in rows})}
        groups = {g.id: g for g in await c.store.get_many(Group, {r.group_id for r in rows})}
        rows = [
            r
            for r in rows
            if r.status in OPEN | {"confirmed"}
            and service.valid(r, events.get(r.concert_id), groups.get(r.group_id))
        ]
        rows.sort(
            key=lambda r: (
                r.date < today(),
                r.date if r.date >= today() else -r.date.toordinal(),
                r.id,
            )
        )
        buttons = [
            (
                (
                    f"{button_date(r.date)} · {groups[r.group_id].name} · "
                    + c.t(
                        "staff_conditional_status"
                        if r.status == "conditional"
                        else "staff_" + r.status
                    )
                ),
                f"sf_view {r.id.hex}",
            )
            for r in rows[page * 15 : (page + 1) * 15]
        ]
        if page:
            buttons.append(("◀", f"my_gigs {page - 1}"))
        if (page + 1) * 15 < len(rows):
            buttons.append(("▶", f"my_gigs {page + 1}"))
        buttons += [
            (c.t("staff_busy_menu"), action(c, "busy")),
            (c.t("staff_personal_calendar"), "cal_scope personal"),
        ]
        return Response(c.t("staff_my"), buttons)
    if command in ("sf_guest", "sf_pdf", "sf_pdf_m", "sf_pdf_l"):
        row = await c.store.get(GigInvitation, UUID(args))
        event, group = (
            await c.store.get(Concert, row.concert_id),
            await c.store.get(Group, row.group_id),
        )
        require(
            row.user_id == c.user.id
            and row.status == "confirmed"
            and service.valid(row, event, group),
            "forbidden",
        )
        lists = await c.store.find(Setlist, group_id=group.id, concert_id=event.id, approved=True)
        require(bool(lists) and lists[0].approved_revision_id is not None, "export_approved_only")
        assert lists[0].approved_revision_id is not None
        revision = await c.store.get(Revision, lists[0].approved_revision_id)
        text = (
            group.name
            + "\n"
            + render_setlist(revision.snapshot, event, c.language, revision.number, approved=True)
        )
        response = Response(text, [(c.t("menu_back"), f"sf_view {row.id.hex}")], monospace=True)
        if command == "sf_pdf":
            return Response(
                c.t("export_choose"),
                [
                    (c.t("export_musicians"), f"sf_pdf_m {row.id.hex}"),
                    (c.t("export_lights"), f"sf_pdf_l {row.id.hex}"),
                    (c.t("menu_back"), f"sf_view {row.id.hex}"),
                ],
            )
        if command in ("sf_pdf_m", "sf_pdf_l"):
            from setlist_bot.application.exports import Exports
            from setlist_bot.infrastructure.pdf import PdfDocuments

            snapshot = revision.snapshot
            if command == "sf_pdf_l":
                snapshot = await Exports(c.store, c.user, c.chat_id)._lighting_snapshot(
                    group.id, snapshot
                )
            payload = {
                "group_name": group.name,
                "concert_date": event.date.isoformat(),
                "concert_title": event.title,
                "city": event.city,
                "version": revision.number,
                "approved": True,
                "snapshot": snapshot,
                "settings": c.user.export_settings,
                "bot_username": c.bot_username,
            }
            response.files = [
                (
                    "setlist.pdf",
                    await asyncio.to_thread(
                        PdfDocuments().render,
                        payload,
                        "lights" if command == "sf_pdf_l" else "musicians",
                        c.language,
                        c.language == "en",
                    ),
                )
            ]
        return response
    require(command == "sf_go", "input")
    session = await c.store.get(Confirmation, UUID(args))
    data = json.loads(session.command)
    require(
        session.chat_id == c.chat_id
        and session.expires_at > now()
        and data.get("kind") == "staff_action"
        and (data.get("owner") is None or data["owner"] == str(c.user.id)),
        "input_expired",
    )
    op = data["operation"]
    if not c.private:
        require(op not in ("respond", "request", "busy", "unbusy"), "private_only")
        # All public mutations are anchored to the originating band's chat.
        group_id = data.get("group_id")
        if data.get("slot_id"):
            group_id = str((await c.store.get(GigSlot, UUID(data["slot_id"]))).group_id)
        if data.get("invitation_id"):
            group_id = str((await c.store.get(GigInvitation, UUID(data["invitation_id"]))).group_id)
        if data.get("replacement_id"):
            group_id = str(
                (await c.store.get(StaffReplacement, UUID(data["replacement_id"]))).group_id
            )
        require(
            group_id is not None
            and bool(await c.store.find(ChatBinding, group_id=UUID(group_id), chat_id=c.chat_id)),
            "forbidden",
        )
    if op == "prompt":
        return await prompt(
            c,
            data["field"],
            group_id=UUID(data["group_id"]) if data.get("group_id") else None,
            event_id=UUID(data["event_id"]) if data.get("event_id") else None,
        )
    if op == "team":
        return await team_view(c, UUID(data["group_id"]))
    if op == "roster":
        slot = await c.store.get(GigSlot, UUID(data["slot_id"]))
        event = await service.event(slot.concert_id)
        group = await c.store.get(Group, event.group_id)
        invites = sorted(
            (
                row
                for row in await c.store.find(GigInvitation, slot_id=slot.id)
                if row.status in OPEN | {"confirmed"} and service.valid(row, event, group)
            ),
            key=lambda row: (row.created_at, row.id),
        )
        page = data.get("page", 0)
        require(page >= 0, "input")
        buttons = []
        for row in invites[page * 15 : (page + 1) * 15]:
            person = await c.store.get(User, row.user_id)
            status = (
                "staff_conditional_status" if row.status == "conditional" else "staff_" + row.status
            )
            buttons.append(
                (
                    (person.display_name or str(person.telegram_id))[:64] + " · " + c.t(status),
                    f"sf_view {row.id.hex}",
                )
            )
        for delta, symbol in ((-1, "◀"), (1, "▶")):
            if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(invites)):
                buttons.append(
                    (symbol, action(c, "roster", slot_id=str(slot.id), page=page + delta))
                )
        buttons.append((c.t("menu_back"), f"sf_lineup {event.id.hex}"))
        return Response(c.t("staff_all_invites"), buttons)
    if op in ("defaults_preview", "defaults_send"):
        if op == "defaults_send":
            await service.invite_defaults(
                UUID(data["event_id"]), data["versions"], force=data.get("force", False)
            )
            return await lineup_view(c, UUID(data["event_id"]))
        event = await service.event(UUID(data["event_id"]))
        slots = await c.store.find(GigSlot, concert_id=event.id)
        filled = {
            row.slot_id
            for row in await c.store.find(GigInvitation, concert_id=event.id, status="confirmed")
        }
        targets = [
            slot
            for slot in slots
            if slot.required and slot.preferred_user_id and slot.id not in filled
        ]
        people = {
            p.id: p
            for p in await c.store.get_many(
                User, {s.preferred_user_id for s in targets if s.preferred_user_id}
            )
        }
        roles = {r.id: r for r in await c.store.get_many(StaffRole, {s.role_id for s in targets})}
        conflicts = await c.store.staffing_conflicts(set(people), event.date, event.id)
        versions, lines, busy_any = {}, [], False
        for slot in targets:
            assert slot.preferred_user_id is not None
            person = people[slot.preferred_user_id]
            busy, _ = conflicts[person.id]
            busy_any |= busy > 0
            role = roles[slot.role_id]
            lines.append(
                f"{role.name[:32]} · {(person.display_name or str(person.telegram_id))[:24]} · "
                + c.t("staff_conflicts" if busy else "staff_unknown")
            )
            versions[str(slot.id)] = slot.version
        require(bool(versions), "staff_filled")
        return Response(
            "\n".join(lines),
            [
                (
                    c.t("staff_force_invite" if busy_any else "staff_invite_defaults"),
                    action(
                        c,
                        "defaults_send",
                        event_id=str(event.id),
                        group_id=str(event.group_id),
                        versions=versions,
                        force=busy_any,
                    ),
                ),
                (c.t("cancel_button"), f"sf_lineup {event.id.hex}"),
            ],
        )
    if op in ("contacts", "contact", "preference"):
        if op == "contacts":
            return await contacts_view(c, UUID(data["group_id"]), data.get("page", 0))
        row = await c.store.get(StaffContact, UUID(data["contact_id"]))
        await c.groups.access(row.group_id, admin=True)
        if op == "preference":
            await service.contact(row.group_id, row.role_id, row.user_id, data["value"])
            return await contacts_view(c, row.group_id)
        person = await c.store.get(User, row.user_id)
        return Response(
            (person.display_name or str(person.telegram_id))
            + "\n"
            + c.t("staff_experience", count=row.worked_count),
            [
                (
                    c.t(label),
                    action(
                        c,
                        "preference",
                        contact_id=str(row.id),
                        group_id=str(row.group_id),
                        value=value,
                    ),
                )
                for label, value in (
                    ("staff_preferred", "preferred"),
                    ("staff_normal", "normal"),
                    ("staff_hidden_label", "hidden"),
                )
            ],
        )
    if op == "templates":
        group_id = UUID(data["group_id"])
        await c.groups.access(group_id, admin=True)
        rows = await c.store.find(LineupTemplate, group_id=group_id)
        buttons = [
            (
                r.name,
                action(
                    c,
                    "template_preview",
                    template_id=str(r.id),
                    group_id=str(group_id),
                    event_id=data.get("event_id"),
                    version=r.version,
                ),
            )
            for r in rows[:40]
        ]
        buttons.append(
            (
                c.t("staff_add_template"),
                action(c, "prompt", field="template", group_id=str(group_id)),
            )
        )
        return Response(c.t("staff_templates"), buttons)
    if op in ("template_preview", "apply"):
        row = await c.store.get(LineupTemplate, UUID(data["template_id"]))
        await c.groups.access(row.group_id, admin=True)
        require(row.version == data["version"], "input_expired")
        if op == "apply":
            await service.apply_template(UUID(data["event_id"]), row.id, row.version)
            return await lineup_view(c, UUID(data["event_id"]))
        text = row.name
        for item in row.slots:
            text += "\n" + (await c.store.get(StaffRole, UUID(item["role_id"]))).name[:48]
            if item.get("user_id"):
                person = await c.store.get(User, UUID(item["user_id"]))
                text += " · " + (person.display_name or str(person.telegram_id))[:32]
        return Response(
            text,
            [
                (
                    c.t("staff_apply"),
                    action(
                        c,
                        "apply",
                        **{
                            k: v
                            for k, v in data.items()
                            if k in ("group_id", "event_id", "template_id", "version")
                        },
                    ),
                )
            ]
            if data.get("event_id")
            else [],
        )
    if op == "needed":
        if not data["value"] and not data.get("confirmed"):
            slot = await c.store.get(GigSlot, UUID(data["slot_id"]))
            await service.event(slot.concert_id)
            active = [
                r
                for r in await c.store.find(GigInvitation, slot_id=slot.id)
                if r.status in OPEN | {"confirmed"}
            ]
            if active:
                return Response(
                    c.t("staff_cancel_check"),
                    [
                        (
                            c.t("confirm_button"),
                            action(
                                c,
                                "needed",
                                slot_id=str(slot.id),
                                version=data["version"],
                                value=False,
                                confirmed=True,
                            ),
                        ),
                        (c.t("cancel_button"), f"sf_lineup {slot.concert_id.hex}"),
                    ],
                )
        slot = await service.needed(UUID(data["slot_id"]), data["version"], data["value"])
        return await lineup_view(c, slot.concert_id)
    if op in ("candidates", "invite"):
        slot = await c.store.get(GigSlot, UUID(data["slot_id"]))
        if op == "invite":
            try:
                row = await service.invite(
                    slot.id,
                    UUID(data["user_id"]),
                    data["version"],
                    force=data.get("force", False),
                    replacement_id=UUID(data["replacement_id"])
                    if data.get("replacement_id")
                    else None,
                )
            except AppError as error:
                if error.code != "staff_busy":
                    raise
                return Response(
                    c.t("staff_busy"),
                    [
                        (
                            c.t("staff_force_invite"),
                            action(
                                c,
                                "invite",
                                **{
                                    k: v
                                    for k, v in data.items()
                                    if k not in ("kind", "operation", "owner", "force")
                                },
                                force=True,
                            ),
                        ),
                        (c.t("cancel_button"), f"sf_lineup {slot.concert_id.hex}"),
                    ],
                )
            return await lineup_view(c, row.concert_id)
        rows = await service.candidates(slot.id)
        page = data.get("page", 0)
        require(page >= 0, "input")
        buttons = []
        for _contact, person, busy, tentative in rows[page * 15 : (page + 1) * 15]:
            label = c.t(
                "staff_conflicts" if busy else "staff_tentative" if tentative else "staff_unknown"
            )
            buttons.append(
                (
                    f"{person.display_name or person.telegram_id} · {label}",
                    action(
                        c,
                        "invite",
                        slot_id=str(slot.id),
                        user_id=str(person.id),
                        version=slot.version,
                        replacement_id=data.get("replacement_id"),
                    ),
                )
            )
        for delta, symbol in ((-1, "◀"), (1, "▶")):
            if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(rows)):
                buttons.append(
                    (
                        symbol,
                        action(
                            c,
                            "candidates",
                            slot_id=str(slot.id),
                            page=page + delta,
                            replacement_id=data.get("replacement_id"),
                        ),
                    )
                )
        buttons += [
            (
                c.t("staff_add_contact"),
                action(c, "prompt", field="contact", group_id=str(slot.group_id)),
            ),
            (c.t("menu_back"), f"sf_lineup {slot.concert_id.hex}"),
        ]
        return Response(c.t("staff_candidates"), buttons)
    if op == "respond":
        try:
            row = await service.respond(
                UUID(data["invitation_id"]),
                data["version"],
                data["answer"],
                force=data.get("force", False),
            )
        except AppError as error:
            if error.code != "staff_busy":
                raise
            return Response(
                c.t("staff_busy"),
                [
                    (
                        c.t("staff_force_yes"),
                        action(
                            c,
                            "respond",
                            **{
                                k: v
                                for k, v in data.items()
                                if k not in ("kind", "operation", "owner", "force")
                            },
                            force=True,
                        ),
                    )
                ],
            )
        return await invitation_view(c, row.id)
    if op == "request":
        row = await service.request_replacement(
            UUID(data["source_id"]), UUID(data["target_id"]) if data.get("target_id") else None
        )
        return Response(
            c.t("staff_waiting"),
            [
                (
                    c.t("staff_cancel_request"),
                    action(c, "cancel_request", replacement_id=str(row.id), version=row.version),
                ),
                (c.t("menu_back"), "my_gigs"),
            ],
        )
    if op == "cancel_request":
        await service.cancel_request(UUID(data["replacement_id"]), data["version"])
        return Response(c.t("ok"), [(c.t("menu_back"), "my_gigs" if c.private else "concerts")])
    if op == "swap":
        row = await service.approve_replacement(UUID(data["invitation_id"]), data["version"])
        return await lineup_view(c, row.concert_id)
    if op == "worked":
        await service.mark_worked(UUID(data["invitation_id"]), data["version"])
        return Response(c.t("ok"))
    if op in ("cancel_preview", "cancel_invite", "retry"):
        row = await c.store.get(GigInvitation, UUID(data["invitation_id"]))
        await service.event(row.concert_id)
        row = await c.store.get(GigInvitation, row.id, lock=True)
        require(row.version == data["version"], "input_expired")
        if op == "cancel_preview":
            return Response(
                c.t("staff_cancel_check"),
                [
                    (
                        c.t("confirm_button"),
                        action(c, "cancel_invite", invitation_id=str(row.id), version=row.version),
                    ),
                    (c.t("cancel_button"), f"sf_lineup {row.concert_id.hex}"),
                ],
            )
        if op == "retry":
            require(
                row.status in ("pending", "reconfirm") and row.expires_at > now(), "input_expired"
            )
            await service.notify(row.user_id, "invitation", row)
        else:
            row.status, row.version = "cancelled", row.version + 1
            await service.cancel_replacements(row.id)
            await service.notify(row.user_id, "cancelled", row)
        return await lineup_view(c, row.concert_id)
    if op in ("busy", "unbusy"):
        require(c.private, "private_only")
        if op == "unbusy":
            await service.unbusy(UUID(data["period_id"]))
        return await busy_view(c)
    raise AppError("input")
