"""Band-level performer assignments; access memberships and gig bookings are independent."""

from typing import Any
from uuid import UUID

from setlist_bot.application.staffing import Staffing
from setlist_bot.application.user_lookup import known_user
from setlist_bot.domain.entities import StaffContact, StaffRole, User
from setlist_bot.domain.rules import require
from setlist_bot.domain.staff_input import role_person
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.response_context import staffing_context
from setlist_bot.transport.staffing import action


async def view(c: Controller, group_id: UUID, page: int = 0) -> Response:
    from setlist_bot.transport.team import members

    return await members(c, group_id, page)


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
        from setlist_bot.application.team import Team

        grouped: dict[UUID, list[str]] = {}
        for name, person in resolved:
            grouped.setdefault(person.id, []).append(name)
        for person_id, names in grouped.items():
            await Team(c.store, c.user).invite(group_id, person_id, names)
        # Existing assignments remain until explicitly removed; editing proposes new roles.
    return await view(c, group_id)


@staffing_context
async def handle(c: Controller, operation: str, data: dict[str, Any]) -> Response | None:
    if operation == "permanent":
        return await view(c, UUID(data["group_id"]), data.get("page", 0))
    if operation not in (
        "permanent_card",
        "permanent_remove",
        "permanent_remove_yes",
        "permanent_move",
        "permanent_preference",
    ):
        return None
    identifier = UUID(data["contact_id"])
    row = await c.store.get(StaffContact, identifier)
    await c.groups.access(row.group_id, admin=True)
    row = await c.store.get(StaffContact, identifier, lock=True)
    require(
        str(row.group_id) == data["group_id"] and row.version == data["version"] and row.permanent,
        "input_expired",
    )
    target = {"group_id": str(row.group_id), "contact_id": str(row.id), "version": row.version}
    service = Staffing(c.store, c.user)
    if operation == "permanent_move":
        await service.move_permanent(row.id, row.version, data["step"])
        target["version"] = row.version
        return await handle(c, "permanent_card", target)
    if operation == "permanent_preference":
        await service.contact(
            row.group_id,
            row.role_id,
            row.user_id,
            "normal" if row.preference == "hidden" else "hidden",
        )
        target["version"] = row.version
        return await handle(c, "permanent_card", target)
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
    ordered = await service.permanent_order(row.group_id, row.role_id)
    people = {
        person.id: person
        for person in await c.store.get_many(User, {contact.user_id for contact in ordered})
    }
    names = []
    active = [contact for contact in ordered if contact.preference != "hidden"]
    for number, contact in enumerate(active, start=1):
        person = people[contact.user_id]
        primary = " · " + c.t("permanent_primary") if number == 1 else ""
        names.append(f"{number}. {person.display_name or person.telegram_id}{primary}")
    for contact in ordered:
        if contact.preference == "hidden":
            person = people[contact.user_id]
            names.append(
                f"{person.display_name or person.telegram_id} · " + c.t("staff_hidden_label")
            )
    text = f"{role.name} · {user.display_name or user.telegram_id}\n\n" + "\n".join(names)
    movement = []
    if row in active:
        position = active.index(row)
        for step, label in ((-1, "permanent_up"), (1, "permanent_down")):
            if 0 <= position + step < len(active):
                movement.append((c.t(label), action(c, "permanent_move", step=step, **target)))
    if user.profile:
        from setlist_bot.transport.profiles import summary

        text += "\n\n" + summary(c, user.profile)
    return Response(
        text,
        [
            (c.t("permanent_edit"), action(c, "prompt", field="permanent", **target)),
            *movement,
            (
                c.t("staff_normal" if row.preference == "hidden" else "staff_hidden_label"),
                action(c, "permanent_preference", **target),
            ),
            (c.t("permanent_remove"), action(c, "permanent_remove", **target)),
            (c.t("menu_back"), action(c, "permanent", group_id=str(row.group_id))),
        ],
    )
