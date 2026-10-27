"""One daily plan; deterministic per-group dispatch between 14:00 and 15:00 Moscow."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import ChatBinding, Concert, Group, Outbox, Reminder, Setlist
from setlist_bot.transport.i18n import tr

MOSCOW = ZoneInfo("Europe/Moscow")


def due_at(group_id: UUID, at: datetime) -> datetime:
    local = at.astimezone(MOSCOW)
    return datetime.combine(local.date(), time(14), MOSCOW) + timedelta(seconds=group_id.int % 3600)


async def plan(store: Store, at: datetime) -> list[tuple[datetime, UUID]]:
    local = at.astimezone(MOSCOW)
    concerts = await store.find(Concert, date=local.date() + timedelta(days=1), deleted_at=None)
    return sorted((due_at(c.group_id, at), c.id) for c in concerts)


async def queue(store: Store, concert_id: UUID, at: datetime) -> None:
    local = at.astimezone(MOSCOW)
    concert = await store.get(Concert, concert_id)
    group = await store.get(Group, concert.group_id, lock=True)
    if (
        concert.deleted_at is not None
        or group.deleted_at is not None
        or not group.reminders_enabled
        or concert.date != local.date() + timedelta(days=1)
        or at < due_at(group.id, at)
    ):
        return
    setlist = (await store.find(Setlist, concert_id=concert.id))[0]
    if setlist.approved or await store.find(Reminder, concert_id=concert.id, day=local.date()):
        return
    bindings = await store.find(ChatBinding, group_id=group.id)
    if not bindings:
        return
    event = Outbox(
        group_id=group.id,
        concert_id=concert.id,
        chat_id=bindings[0].chat_id,
        text=tr(group.language, "reminder_text", title=f"{concert.date:%d.%m.%Y} {concert.title}"),
        buttons=[
            [tr(group.language, "reminder_open"), f"use_concert {concert.id}"],
            [tr(group.language, "reminder_disable"), f"reminders off {group.id}"],
        ],
    )
    store.add(event)
    await store.flush()
    store.add(
        Reminder(group_id=group.id, concert_id=concert.id, day=local.date(), outbox_id=event.id)
    )
