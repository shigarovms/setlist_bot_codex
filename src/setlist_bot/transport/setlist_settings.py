"""Collective-wide presentation and suggestion policy, separate from PDF options."""

from typing import Any
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.domain.entities import Confirmation, Membership
from setlist_bot.domain.rules import require
from setlist_bot.transport.controller import Controller, Response


async def settings_menu(
    c: Controller, row: Confirmation, data: dict[str, Any], action: str
) -> Response:
    group = await c.groups.access(UUID(data["group_id"]))
    membership = (await c.store.find(Membership, group_id=group.id, user_id=c.user.id))[0]
    if action == "back":
        response = await c._refresh_card(row, data)
        assert response.menu_card_id is not None
        fresh = await c.store.get(Confirmation, response.menu_card_id)
        response.buttons = await c._menu_buttons(fresh, c.menus.data(fresh), "options")
        return response
    require(action in ("main", "mono", "article", "toggle"), "input")
    response = Response("", markup_only=True, edit_message_ids=list(data["message_ids"]))
    if action != "main":
        await c.groups.access(group.id, admin=True)
        if action == "toggle":
            group.admins_only_suggestions = not group.admins_only_suggestions
        else:
            group.setlist_style = action
        record_activity(
            c.store,
            c.user,
            "group_setlist_settings",
            group.id,
            style=group.setlist_style,
            restricted=group.admins_only_suggestions,
        )
        response = await c._refresh_card(row, data)
        assert response.menu_card_id is not None
        row = await c.store.get(Confirmation, response.menu_card_id)
        await queue_card_refreshes(c.store, group.id)
    key = row.id.hex
    buttons = []
    if membership.role == "admin":
        for style in ("mono", "article"):
            buttons.append(
                (
                    ("✅ " if group.setlist_style == style else "") + c.t("style_" + style),
                    f"slsettings {key} {style}",
                )
            )
        buttons.append(
            (
                c.t(
                    "restricted_toggle",
                    state=c.t(
                        "restricted_on" if group.admins_only_suggestions else "restricted_off"
                    ),
                ),
                f"slsettings {key} toggle",
            )
        )
    else:
        buttons.append((c.t("style_" + group.setlist_style), f"slsettings {key} main"))
    buttons.append((c.t("menu_back"), f"slsettings {key} back"))
    response.buttons = buttons
    return response
