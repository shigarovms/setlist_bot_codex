"""Shared publication adapter for Telegram commands and the Mini App HTTP transport."""

from uuid import UUID

from setlist_bot.application.groups import Groups
from setlist_bot.application.ports import Store
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import ChatBinding, Concert, Outbox, User
from setlist_bot.domain.rules import require
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.render import render_setlist


async def queue_publication(
    store: Store,
    user: User,
    group_id: UUID,
    concert_id: UUID,
    expected: int,
    *,
    origin_chat_id: int | None = None,
) -> Outbox:
    groups, lists = Groups(store, user), Setlists(store, user)
    await groups.can_suggest(group_id)
    group = await groups.access(group_id)
    snapshot, version, _ = await lists.read(group_id, concert_id, private=True)
    require(version == expected, "conflict")
    bindings = await store.find(ChatBinding, group_id=group_id)
    require(bool(bindings), "no_chat")
    require(origin_chat_id is None or bindings[0].chat_id == origin_chat_id, "miniapp_expired")
    require(any(block["items"] for block in snapshot["blocks"]), "empty_setlist")
    submission = await lists.publish(group_id, concert_id)
    _, shared_version, _ = await lists.read(group_id, concert_id)
    submission.snapshot = {**submission.snapshot, "shared_version": shared_version}
    prefix = tr(group.language, "submission", name=await groups.name(group_id)) + "\n\n"
    body = (
        group.name
        + "\n"
        + render_setlist(
            snapshot, await store.get(Concert, concert_id), group.language, version, proposal=True
        )
    )
    event = Outbox(
        chat_id=bindings[0].chat_id,
        text=prefix + body,
        group_id=group_id,
        concert_id=concert_id,
        monospace=True,
        monospace_offset=len(prefix),
        actor=f"telegram:{user.telegram_id}",
        payload={"setlist_snapshot": snapshot, "setlist_version": version},
        buttons=[[tr(group.language, "submission_apply"), f"apply_submission {submission.id}"]],
    )
    store.add(event)
    return event
