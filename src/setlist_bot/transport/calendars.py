import json
import re
from datetime import timedelta
from uuid import UUID

from setlist_bot.application.calendars import Calendars
from setlist_bot.domain.entities import CalendarSubscription, Confirmation, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.transport.controller import Controller, Response


def link_response(
    c: Controller, identifier: UUID, token: str, *, instructions: bool = False
) -> Response:
    url = c.calendar_public_base_url + "/calendar/" + token + ".ics"
    return Response(
        (c.t("calendar_help") + "\n\n" if instructions else "") + c.t("calendar_link", url=url),
        [
            (c.t("calendar_help_button"), f"cal_help {identifier.hex}"),
            (c.t("menu_back"), "calendar"),
        ],
        monospace=True,
        monospace_body=url,
    )


async def show_link(
    c: Controller, service: Calendars, identifier: UUID, *, instructions: bool = False
) -> Response:
    row = await service.owned(identifier)
    try:
        token = await service.reveal(identifier)
    except AppError as exc:
        if str(exc) != "calendar_link_unreadable":
            raise
        token = None
    if token:
        return link_response(c, row.id, token, instructions=instructions)
    return Response(
        c.t("calendar_legacy"),
        [
            (c.t("calendar_restore"), f"cal_restore {row.id.hex}"),
            (c.t("calendar_rotate"), f"cal_change {row.id.hex} rotate"),
            (c.t("menu_back"), "calendar"),
        ],
    )


async def restore_link_input(c: Controller, session: Confirmation, value: str) -> Response:
    data = json.loads(session.command)
    require(
        c.private
        and session.user_id == c.user.id
        and session.chat_id == c.chat_id
        and session.expires_at > now()
        and data.get("kind") == "calendar_restore",
        "input_expired",
    )
    prefix = re.escape(c.calendar_public_base_url + "/calendar/")
    match = re.fullmatch(prefix + r"([A-Za-z0-9_-]{43})\.ics", value.strip())
    service = Calendars(c.store, c.user, c.calendar_link_key)
    try:
        require(match is not None, "calendar_wrong_link")
        assert match is not None
        await service.restore(UUID(data["subscription_id"]), data["version"], match[1])
    except AppError as exc:
        if str(exc) != "calendar_wrong_link":
            raise
        return Response(
            c.t("calendar_wrong_link") + "\n\n" + c.t("calendar_restore_prompt"),
            force_reply=True,
            workflow_id=session.id,
            input_rejected=True,
        )
    result = link_response(c, UUID(data["subscription_id"]), match[1])
    result.delete_input = True
    result.delete_message_ids = data.get("message_ids", [])
    await c.store.delete(session)
    return result


async def handle_calendar(c: Controller, command: str, args: str) -> Response:
    if not c.private:
        return Response(
            c.t("calendar_private"),
            [(c.t("calendar_entry"), f"https://t.me/{c.bot_username}?start=calendar")],
        )
    if not c.calendar_public_base_url:
        return Response(c.t("calendar_not_ready"))
    service = Calendars(c.store, c.user, c.calendar_link_key)
    back = (c.t("menu_back"), "calendar")
    if command == "cal_help":
        return (
            await show_link(c, service, UUID(args), instructions=True)
            if args
            else Response(c.t("calendar_help"), [(c.t("calendar_links"), "cal_links"), back])
        )
    if command == "cal_show":
        return await show_link(c, service, UUID(args))
    if command == "cal_restore":
        restore_row = await service.owned(UUID(args))
        session = Confirmation(
            user_id=c.user.id,
            chat_id=c.chat_id,
            expires_at=now() + timedelta(minutes=10),
            command=json.dumps(
                {
                    "kind": "calendar_restore",
                    "subscription_id": str(restore_row.id),
                    "version": restore_row.version,
                }
            ),
        )
        c.store.add(session)
        return Response(c.t("calendar_restore_prompt"), force_reply=True, workflow_id=session.id)
    if command in ("calendar", "cal_links"):
        groups = await service.groups()
        page = int(args) if args else 0
        require(0 <= page <= 10000, "input")
        if command == "calendar":
            choices = [(c.t("calendar_all"), "cal_scope all")] + [
                (g.name, f"cal_scope {g.id.hex}") for g in groups
            ]
        else:
            names = {g.id: g.name for g in groups}
            rows = await c.store.find(CalendarSubscription, owner_id=c.user.id, enabled=True)
            rows.sort(key=lambda r: (r.created_at, r.id))
            choices = [
                (
                    c.t("calendar_all")
                    if row.group_id is None
                    else names.get(row.group_id, c.t("calendar_unavailable_group")),
                    f"cal_scope {'all' if row.group_id is None else row.group_id.hex}",
                )
                for row in rows
            ]
        buttons = choices[page * 20 : (page + 1) * 20]
        if page:
            buttons.append((c.t("music_prev"), f"{command} {page - 1}"))
        if (page + 1) * 20 < len(choices):
            buttons.append((c.t("music_next"), f"{command} {page + 1}"))
        buttons.append((c.t("calendar_links"), "cal_links") if command == "calendar" else back)
        context = await c.groups.context(c.chat_id, True)
        buttons.append(
            (
                c.t("back_concerts"),
                f"group_concerts {context.group_id.hex}" if context.group_id else "groups",
            )
        )
        return Response(c.t("calendar_menu"), buttons)
    if command in ("cal_scope", "cal_create"):
        group_id = None if args == "all" else UUID(args)
        groups_by_id = {g.id: g for g in await service.groups()}
        row = await service.subscription(group_id)
        require(group_id is None or group_id in groups_by_id or row is not None, "forbidden")
        if command == "cal_create":
            row, token = await service.create(group_id)
            if token:
                return link_response(c, row.id, token)
            return await show_link(c, service, row.id)
        if row is not None and row.enabled:
            title = (
                c.t("calendar_all")
                if group_id is None
                else (
                    groups_by_id[group_id].name
                    if group_id in groups_by_id
                    else c.t("calendar_unavailable_group")
                )
            )
            buttons = [(c.t("calendar_disable"), f"cal_change {row.id.hex} disable"), back]
            if group_id is None or group_id in groups_by_id:
                buttons.insert(0, (c.t("calendar_rotate"), f"cal_change {row.id.hex} rotate"))
                buttons.insert(0, (c.t("calendar_help_button"), f"cal_help {row.id.hex}"))
                buttons.insert(0, (c.t("calendar_show"), f"cal_show {row.id.hex}"))
            return Response(title + "\n\n" + c.t("calendar_enabled"), buttons)
        return Response(
            c.t("calendar_disabled"), [(c.t("calendar_create"), "cal_create " + args), back]
        )
    if command == "cal_change":
        identifier, action = args.split()
        require(action in ("disable", "rotate"), "input")
        row = await c.store.get(CalendarSubscription, UUID(identifier))
        require(row.owner_id == c.user.id and row.enabled, "input_expired")
        session = Confirmation(
            user_id=c.user.id,
            chat_id=c.chat_id,
            expires_at=now() + timedelta(minutes=10),
            command=json.dumps(
                {
                    "kind": "calendar_change",
                    "subscription_id": str(row.id),
                    "version": row.version,
                    "action": action,
                }
            ),
        )
        c.store.add(session)
        return Response(
            c.t("calendar_confirm_" + action),
            [
                (c.t("confirm_button"), f"cal_confirm {session.id.hex}"),
                (c.t("cancel_button"), f"cal_cancel {session.id.hex}"),
            ],
            workflow_id=session.id,
        )
    if command in ("cal_confirm", "cal_cancel"):
        session = await c.store.get(Confirmation, UUID(args), lock=True)
        data = json.loads(session.command)
        require(
            session.user_id == c.user.id
            and session.chat_id == c.chat_id
            and session.expires_at > now()
            and data.get("kind") == "calendar_change",
            "input_expired",
        )
        if command == "cal_cancel":
            result = Response(c.t("ok"), [back])
        else:
            changed, token = await service.change(
                UUID(data["subscription_id"]), data["version"], data["action"]
            )
            result = (
                link_response(c, changed.id, token)
                if token
                else Response(c.t("calendar_disabled"), [back])
            )
        result.delete_message_ids.extend(data.get("message_ids", []))
        await c.store.delete(session)
        return result
    raise ValueError("Unknown calendar action")
