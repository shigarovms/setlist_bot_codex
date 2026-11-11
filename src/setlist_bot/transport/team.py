"""Band membership views; callbacks address a membership, never the selected context."""

from uuid import UUID

from setlist_bot.application.team import Team
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import (
    Concert,
    GigInvitation,
    Group,
    Membership,
    StaffContact,
    StaffRole,
)
from setlist_bot.domain.rules import require
from setlist_bot.transport.controller import Controller, Response


async def invitation(c: Controller, member: Membership) -> Response:
    require(c.private and member.user_id == c.user.id, "forbidden")
    group = await Team(c.store, c.user).validate(member)
    assert member.invitation is not None
    return Response(
        c.t(
            "team_roles_invitation" if member.status == "active" else "team_invitation",
            group=group.name,
            roles=", ".join(member.invitation["roles"]),
        ),
        [
            (
                c.t("team_accept_roles" if member.status == "active" else "team_accept"),
                f"tm_yes {member.id.hex} {member.version}",
            ),
            (c.t("team_decline"), f"tm_no {member.id.hex} {member.version}"),
        ],
    )


async def members(c: Controller, group_id: UUID, page: int = 0) -> Response:
    group = await c.groups.access(group_id, admin=True)
    if not c.private:
        from setlist_bot.domain.entities import ChatBinding

        require(
            bool(await c.store.find(ChatBinding, group_id=group_id, chat_id=c.chat_id)), "forbidden"
        )
    require(page >= 0, "input")
    rows = [
        m
        for m in await c.store.find(Membership, group_id=group_id)
        if m.status != "left" or m.invitation
    ]
    roles = {r.id: r.name for r in await c.store.find(StaffRole, group_id=group_id)}
    contacts = await c.store.find(StaffContact, group_id=group_id, permanent=True)
    from setlist_bot.domain.entities import User

    people = {u.id: u for u in await c.store.get_many(User, {m.user_id for m in rows})}
    lines, buttons = [group.name, "", c.t("permanent_title")], []
    for i, member in enumerate(sorted(rows, key=lambda m: (m.user_id != group.owner_id, m.id))):
        person = people[member.user_id]
        name = (
            person.profile.get("name")
            or member.musical_role
            or person.display_name
            or str(person.telegram_id)
        )
        names = [roles[r.role_id] for r in contacts if r.user_id == member.user_id]
        right = "owner" if member.user_id == group.owner_id else member.role
        lines.append(
            f"{name} · {c.t(right)} · {c.t(member.status)} · "
            + (", ".join(names) or c.t("join_no_roles"))
        )
        if member.invitation:
            lines.append(c.t("team_wait") + ": " + ", ".join(member.invitation["roles"]))
        if member.requested_roles:
            lines.append(c.t("team_pending") + ": " + ", ".join(member.requested_roles))
        if not page * 10 <= i < (page + 1) * 10:
            continue
        for contact in contacts:
            if contact.user_id == member.user_id:
                from setlist_bot.transport.staffing import action

                buttons.append(
                    (
                        f"{name} · {roles[contact.role_id]}",
                        action(
                            c,
                            "permanent_card",
                            group_id=str(group_id),
                            contact_id=str(contact.id),
                            version=contact.version,
                        ),
                    )
                )
        if member.invitation:
            buttons.extend(
                (c.t(key) + " · " + name, f"tm_{op} {member.id.hex} {member.version}")
                for op, key in (("retry", "team_retry"), ("cancel", "team_cancel"))
            )
        if member.status == "pending" or member.requested_roles is not None:
            buttons.extend(
                (c.t(key) + " · " + name, f"tm_{op} {member.id.hex} {member.version}")
                for op, key in (("approve", "approve_button"), ("reject", "reject_button"))
            )
        if member.status == "active" and member.user_id != group.owner_id:
            if member.role != "admin" or c.user.id == group.owner_id or member.user_id == c.user.id:
                buttons.append(
                    (
                        c.t("team_leave" if member.user_id == c.user.id else "team_remove")
                        + " · "
                        + name,
                        f"tm_remove {member.id.hex} {member.version}",
                    )
                )
            if c.user.id == group.owner_id:
                operation = "revoke" if member.role == "admin" else "grant"
                buttons.append(
                    (
                        c.t(operation) + " · " + name,
                        f"tm_rights {member.id.hex} {member.version} {operation}",
                    )
                )
    if page:
        buttons.append(("◀", f"tm_list {group_id.hex} {page - 1}"))
    if (page + 1) * 10 < len(rows):
        buttons.append(("▶", f"tm_list {group_id.hex} {page + 1}"))
    from setlist_bot.transport.staffing import action

    buttons.extend(
        [
            (c.t("permanent_add"), action(c, "prompt", field="permanent", group_id=str(group_id))),
            (c.t("menu_back"), action(c, "team", group_id=str(group_id))),
        ]
    )
    return Response("\n".join(lines), buttons)


async def handle(c: Controller, command: str, args: str) -> Response:
    fields = args.split()
    require(bool(fields), "input")
    if command == "tm_list":
        return await members(c, UUID(fields[0]), int(fields[1]) if len(fields) > 1 else 0)
    member = await c.store.get(Membership, UUID(fields[0]))
    if not c.private:
        from setlist_bot.domain.entities import ChatBinding

        require(
            bool(await c.store.find(ChatBinding, group_id=member.group_id, chat_id=c.chat_id)),
            "forbidden",
        )
    service = Team(c.store, c.user)
    if command == "tm_view":
        return await invitation(c, member)
    require(len(fields) >= 2, "input")
    version = int(fields[1])
    if command in ("tm_yes", "tm_no"):
        require(c.private, "private_only")
        member = await service.respond(member.id, version, command == "tm_yes")
        group = await c.store.get(Group, member.group_id)
        return Response(
            group.name
            + "\n\n"
            + c.t(
                "team_joined" if command == "tm_yes" else "team_declined",
                name=await c.groups.name(group.id),
            ),
            [(c.t("my_groups"), "groups")],
        )
    if command in ("tm_remove", "tm_leave_yes"):
        group = await service.check_remove(member)
        require(member.version == version, "input_expired")
        if command == "tm_leave_yes":
            await service.remove(member.id, version)
            return Response(
                group.name + "\n\n" + c.t("team_removed"), [(c.t("my_groups"), "groups")]
            )
        entries = []
        buttons = []
        for row in await c.store.find(
            GigInvitation, group_id=group.id, user_id=member.user_id, status="confirmed"
        ):
            event = await c.store.get(Concert, row.concert_id)
            if not event.deleted_at and event.date >= today():
                entries.append(f"{event.date:%d.%m.%Y} · {event.title}")
                buttons.append((f"{event.date:%d.%m.%Y} · {event.title}", f"sf_view {row.id.hex}"))
        return Response(
            group.name
            + "\n\n"
            + c.t("team_remove_check", name=await c.groups.name(group.id, member.user_id))
            + "\n\n"
            + ("\n".join(entries) or c.t("team_no_gigs")),
            [
                (c.t("confirm_button"), f"tm_leave_yes {member.id.hex} {version}"),
                (c.t("cancel_button"), "groups"),
                *buttons,
            ],
        )
    group = await c.groups.access(member.group_id, admin=True)
    require(member.version == version, "input_expired")
    if command == "tm_cancel":
        await service.cancel(member.id, version)
    elif command == "tm_retry":
        require(member.invitation is not None, "input_expired")
        assert member.invitation is not None
        roles = member.invitation["roles"]
        member.invitation = None
        await service.invite(group.id, member.user_id, roles)
    elif command in ("tm_approve", "tm_reject"):
        await c.groups.decide_member(group.id, member.id, command == "tm_approve", version)
    elif command == "tm_rights":
        require(len(fields) == 3 and fields[2] in ("grant", "revoke"), "input")
        require(group.owner_id == c.user.id, "owner_only")
        context = await c.groups.context(c.chat_id, c.private)
        context.group_id = group.id
        return await c.execute(f"administrator {fields[2]} {member.id}")
    else:
        require(False, "input")
    return await members(c, group.id)
