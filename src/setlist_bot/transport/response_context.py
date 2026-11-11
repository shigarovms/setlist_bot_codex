"""Context captured from immutable targets, displayed only after successful authorization."""

from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from setlist_bot.domain.concerts import button_date
from setlist_bot.domain.entities import (
    Concert,
    GigInvitation,
    GigSlot,
    Group,
    LineupTemplate,
    StaffContact,
    StaffReplacement,
    StaffRole,
)

if TYPE_CHECKING:
    from setlist_bot.transport.controller import Controller, Response


def prefix(response: "Response", heading: str) -> "Response":
    if not response.text or response.markup_only or not heading:
        return response
    if response.text == heading or response.text.startswith((heading + "\n", heading + " · ")):
        return response
    response.text = heading + "\n\n" + response.text
    return response


def apply(response: "Response", group_id: UUID | None, headings: list[str]) -> "Response":
    if group_id is None or response.context_group is not None:
        return response
    if not (
        response.text == headings[0]
        or response.text.startswith((headings[0] + "\n", headings[0] + " · "))
    ):
        for heading in reversed(headings):
            prefix(response, heading)
    response.context_group = group_id
    response.context_prefix = headings[0] + "\n\n"
    return response


async def resolve(c: "Controller", data: dict[str, Any]) -> tuple[UUID | None, list[str]]:
    group_id = UUID(data["group_id"]) if data.get("group_id") else None
    event_id = UUID(data["event_id"]) if data.get("event_id") else None
    extra = []
    for key, kind in (
        ("invitation_id", GigInvitation),
        ("source_id", GigInvitation),
        ("replacement_id", StaffReplacement),
        ("slot_id", GigSlot),
        ("contact_id", StaffContact),
        ("template_id", LineupTemplate),
    ):
        if data.get(key):
            obj: Any = await c.store.get(kind, UUID(data[key]))
            group_id = obj.group_id
            event_id = getattr(obj, "concert_id", event_id)
            if isinstance(obj, LineupTemplate):
                extra.append(obj.name)
                index = data.get("index")
                if isinstance(index, int) and 0 <= index < len(obj.slots):
                    extra.append(
                        (await c.store.get(StaffRole, UUID(obj.slots[index]["role_id"]))).name
                    )
            invitation = (
                obj
                if isinstance(obj, GigInvitation)
                else (
                    await c.store.get(GigInvitation, obj.source_id)
                    if isinstance(obj, StaffReplacement)
                    else None
                )
            )
            if invitation:
                slot = await c.store.get(GigSlot, invitation.slot_id)
                extra.append((await c.store.get(StaffRole, slot.role_id)).name)
                extra.append(await c.groups.name(invitation.group_id, invitation.user_id))
            if isinstance(obj, (GigSlot, StaffContact)):
                extra.append((await c.store.get(StaffRole, obj.role_id)).name)
            if isinstance(obj, StaffContact):
                extra.append(await c.groups.name(obj.group_id, obj.user_id))
    if (
        data.get("user_id")
        and group_id
        and not any(
            data.get(key) for key in ("invitation_id", "source_id", "replacement_id", "contact_id")
        )
    ):
        extra.append(await c.groups.name(group_id, UUID(data["user_id"])))
    event = await c.store.get(Concert, event_id) if event_id else None
    if event:
        if group_id and event.group_id != group_id:
            raise ValueError("Response context belongs to a different band")
        group_id = event.group_id
    if group_id is None:
        return None, []
    headings = [(await c.store.get(Group, group_id)).name]
    if event:
        headings.append(
            " · ".join(p for p in (button_date(event.date), event.title, event.city) if p)
        )
    elif data.get("uncreated_event"):
        payload = data["uncreated_event"]
        headings.append(
            " · ".join(
                p
                for p in (
                    button_date(date.fromisoformat(payload["date"])),
                    payload.get("title"),
                    payload.get("city"),
                )
                if p
            )
        )
    if extra:
        headings.append(" · ".join(dict.fromkeys(extra)))
    return group_id, headings


async def band(
    c: "Controller", response: "Response", group_id: UUID, event_id: UUID | None = None
) -> "Response":
    if response.context_group is not None:
        return response
    target, headings = await resolve(
        c, {"group_id": str(group_id), "event_id": str(event_id) if event_id else None}
    )
    return apply(response, target, headings)


def staffing_context[F: Callable[..., Any]](function: F) -> F:
    """Snapshot context before a handler consumes its session or deletes the target."""
    import functools
    import inspect
    import json

    @functools.wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(function).bind(*args, **kwargs).arguments
        c = bound["c"]
        data = dict(bound.get("data", {}))
        data.update(bound.get("extra", {}))
        if function.__name__ == "choices" and bound.get("payload"):
            data["uncreated_event"] = bound["payload"]
        row = bound.get("session") or bound.get("row")
        if row is not None and hasattr(row, "command"):
            data.update(json.loads(row.command))
        for key in ("group_id", "event_id"):
            if bound.get(key):
                data[key] = str(bound[key])
        command = bound.get("command")
        raw = bound.get("args", "")
        if isinstance(raw, dict):
            data.update(raw)
        if command == "sf_go":
            from setlist_bot.domain.entities import Confirmation

            data.update(json.loads((await c.store.get(Confirmation, UUID(raw))).command))
        elif command in ("sf_lineup", "sf_view", "sf_team"):
            key = {"sf_lineup": "event_id", "sf_view": "invitation_id", "sf_team": "group_id"}[
                command
            ]
            data[key] = raw.split()[0]
        if command == "sf_view" and not c.private:
            return await function(*args, **kwargs)
        target, headings = await resolve(c, data)
        result = await function(*args, **kwargs)
        return apply(result, target, headings) if result is not None else None

    return cast(F, wrapped)
