"""Deliver setlist approval and reopening requests through the existing bot."""

from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlist_menus import REQUEST, SetlistMenus
from setlist_bot.domain.entities import Concert, Confirmation, Group, Membership, Outbox, User
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.render import concert_label, render_setlist


async def queue_admin_request(store: Store, actor: User, request: Confirmation) -> None:
    data = SetlistMenus.data(request)
    group = await store.get(Group, UUID(data["group_id"]))
    concert = await store.get(Concert, UUID(data["concert_id"]))
    admin = await store.get(User, request.user_id)
    header = tr(
        admin.language,
        "request_admin",
        name=await Groups(store, actor).name(group.id),
        group=group.name,
        action=tr(
            admin.language, "request_approve" if data["operation"] == "approve" else "request_edit"
        ),
    )
    store.add(
        Outbox(
            group_id=group.id,
            concert_id=concert.id,
            chat_id=admin.telegram_id,
            text=header
            + "\n\n"
            + render_setlist(
                data["snapshot"],
                concert,
                admin.language,
                data["version"],
                approved=data["approved"],
            ),
            buttons=[
                [tr(admin.language, "request_yes"), f"slreq {request.id.hex} yes"],
                [tr(admin.language, "request_no"), f"slreq {request.id.hex} no"],
            ],
        )
    )


async def queue_decision_result(store: Store, row: Confirmation) -> None:
    data = SetlistMenus.data(row)
    group = await store.get(Group, UUID(data["group_id"]))
    concert = await store.get(Concert, UUID(data["concert_id"]))
    requester = await store.get(User, UUID(data["requester_id"]))
    private = data["private_chat"]
    language = requester.language if private else group.language
    text = tr(
        language,
        "request_result",
        action=tr(language, "request_edit" if data["operation"] == "reopen" else "request_approve"),
        result=tr(
            language, "request_accepted" if data["status"] == "accepted" else "request_rejected"
        ),
    )
    store.add(
        Outbox(
            group_id=group.id,
            concert_id=concert.id,
            chat_id=data["source_chat_id"],
            support_kind="sl_result" if private else "",
            payload={"request_id": str(row.id)} if private else {},
            text=f"{group.name}\n{concert_label(concert, language)}\n\n{text}",
        )
    )


async def can_deliver_private_decision(
    store: Store, event: Outbox, group: Group, concert: Concert
) -> bool:
    rows = await store.find(Confirmation, id=UUID(event.payload["request_id"]))
    if not rows:
        return False
    data = SetlistMenus.data(rows[0])
    if not (
        data.get("kind") == REQUEST
        and data.get("status") in ("accepted", "rejected")
        and data["private_chat"]
        and data["source_chat_id"] == event.chat_id > 0
        and data["group_id"] == str(group.id)
        and data["concert_id"] == str(concert.id)
        and data["group_epoch"] == group.lifecycle_version
        and data["concert_epoch"] == concert.lifecycle_version
    ):
        return False
    requester = await store.get(User, UUID(data["requester_id"]))
    return (
        requester.telegram_id == event.chat_id
        and bool(
            await store.find(
                Membership,
                group_id=group.id,
                user_id=requester.id,
                status="active",
            )
        )
        and concert.id in await store.visible_concert_ids(requester.id, group.id)
    )
