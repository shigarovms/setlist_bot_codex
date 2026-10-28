import re
from uuid import UUID

from setlist_bot.application.activity import record_activity
from setlist_bot.application.groups import Groups
from setlist_bot.application.message_refresh import queue_card_refreshes
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Concert, User
from setlist_bot.domain.rules import parse_pause, require

FIELDS = {"concert_duration", "concert_city", "concert_info"}


class ConcertDetails:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def update(self, group_id: UUID, concert_id: UUID, field: str, value: str) -> Concert:
        await Groups(self.store, self.user).access(group_id, admin=True)
        concert = await self.store.get(Concert, concert_id)
        require(concert.group_id == group_id and concert.deleted_at is None, "not_found")
        require(field in FIELDS, "input")
        text = value.strip()
        if field == "concert_duration":
            if re.fullmatch(r"\d{1,2}:\d{2}", text):
                text += ":00"
            seconds = (
                None
                if text == "-"
                else parse_pause(text + " мин" if text.isascii() and text.isdecimal() else text)
            )
            require(text == "-" or seconds is not None, "pause_duration")
            concert.performance_duration_seconds = seconds
        else:
            require(0 < len(text) <= (200 if field == "concert_city" else 2000), "input")
            setattr(
                concert,
                "city" if field == "concert_city" else "extra_info",
                "" if text == "-" else text,
            )
        concert.metadata_version += 1
        record_activity(
            self.store,
            self.user,
            "concert_details",
            concert_id,
            field=field,
            version=concert.metadata_version,
        )
        await queue_card_refreshes(self.store, group_id, concert_id)
        return concert
