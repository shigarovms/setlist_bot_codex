"""Band-level performer assignments; access memberships and gig bookings are independent."""

from typing import Any
from uuid import UUID

from setlist_bot.application.staffing import Staffing
from setlist_bot.application.user_lookup import known_user
from setlist_bot.domain.entities import StaffContact, StaffRole, User
from setlist_bot.domain.rules import require
from setlist_bot.domain.staff_input import role_person
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.staffing import action


async def view(c: Controller, group_id: UUID, page: int = 0) -> Response:
    group = await c.groups.access(group_id, admin=True)
    require(page >= 0, "input")
    roles = {r.id: r for r in await c.store.find(StaffRole, group_id=group_id)}
    rows = sorted(
        (r for r in await c.store.find(StaffContact, group_id=group_id) if r.permanent),
        key=lambda r: (roles[r.role_id].name_key, r.priority, r.id),
    )
    people = {u.id: u for u in await c.store.get_many(User, {r.user_id for r in rows})}
    lines = [
        f"{i}. {roles[r.role_id].name}: "
        f"{people[r.user_id].display_name or people[r.user_id].telegram_id}"
        for i, r in enumerate(rows, 1)
    ]
    buttons = [
        (
            lines[i],
            action(
                c, "permanent_card", group_id=str(group_id), contact_id=str(r.id), version=r.version
            ),
        )
        for i, r in enumerate(rows)
        if page * 15 <= i < (page + 1) * 15
    ]
    for delta, symbol in ((-1, "◀"), (1, "▶")):
        if page + delta >= 0 and (delta < 0 or (page + 1) * 15 < len(rows)):
            buttons.append(
                (symbol, action(c, "permanent", group_id=str(group_id), page=page + delta))
            )
    buttons += [
        (c.t("permanent_add"), action(c, "prompt", field="permanent", group_id=str(group_id))),
        (c.t("menu_back"), action(c, "team", group_id=str(group_id))),
    ]
    return Response(
        group.name + "\n\n" + c.t("permanent_title") + "\n\n" + ("\n".join(lines) or c.t("empty")),
        buttons,
    )


async def input_value(c: Controller, data: dict[str, Any], value: str) -> Response | None:
    if data["operation"] not in ("permanent", "permanent_priority"):
        return None
    group_id = UUID(data["group_id"])
    service = Staffing(c.store, c.user)
    old = (
        await c.store.get(StaffContact, UUID(data["contact_id"]))
        if data.get("contact_id")
        else None
    )
    if old:
        require(old.group_id == group_id and old.version == data["version"], "input_expired")
    if data["operation"] == "permanent_priority":
        require(old is not None and value.strip().isdigit(), "input")
        assert old is not None
        await service.change_contact(old.id, old.version, priority=int(value))
    else:
        lines = [s.strip() for s in value.splitlines() if s.strip()]
        require(0 < len(lines) <= 40 and (old is None or len(lines) == 1), "input")
        # RU/EN: Resolve every account first: unknown users must not leave partial assignments.
        values = [role_person(line, required=True) for line in lines]
        resolved = [(role, await known_user(c.store, ref)) for role, ref in values]
        for name, person in resolved:
            role = await service.role(group_id, name)
            row = await service.set_permanent(group_id, role.id, person.id)
            if old and old.id != row.id:
                await service.change_contact(old.id, old.version, remove=True)
    return await view(c, group_id)


async def handle(c: Controller, operation: str, data: dict[str, Any]) -> Response | None:
    if operation == "permanent":
        return await view(c, UUID(data["group_id"]), data.get("page", 0))
    if operation not in ("permanent_card", "permanent_remove", "permanent_remove_yes"):
        return None
    row = await c.store.get(StaffContact, UUID(data["contact_id"]))
    await c.groups.access(row.group_id, admin=True)
    require(
        str(row.group_id) == data["group_id"] and row.version == data["version"] and row.permanent,
        "input_expired",
    )
    target = {"group_id": str(row.group_id), "contact_id": str(row.id), "version": row.version}
    if operation == "permanent_remove_yes":
        await Staffing(c.store, c.user).change_contact(row.id, row.version, remove=True)
        return await view(c, row.group_id)
    if operation == "permanent_remove":
        return Response(
            c.t("permanent_remove_check"),
            [
                (c.t("confirm_button"), action(c, "permanent_remove_yes", **target)),
                (c.t("cancel_button"), action(c, "permanent", group_id=str(row.group_id))),
            ],
        )
    user = await c.store.get(User, row.user_id)
    role = await c.store.get(StaffRole, row.role_id)
    text = f"{role.name}: {user.display_name or user.telegram_id}\n" + c.t(
        "permanent_priority", value=row.priority
    )
    if user.profile:
        from setlist_bot.transport.profiles import summary

        text += "\n\n" + summary(c, user.profile)
    return Response(
        text,
        [
            (c.t("permanent_edit"), action(c, "prompt", field="permanent", **target)),
            (c.t("permanent_order"), action(c, "prompt", field="permanent_priority", **target)),
            (c.t("permanent_remove"), action(c, "permanent_remove", **target)),
            (c.t("menu_back"), action(c, "permanent", group_id=str(row.group_id))),
        ],
    )
