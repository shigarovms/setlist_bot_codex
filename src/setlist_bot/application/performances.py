"""RU: Личный список всех выступлений. EN: Read-only cross-group overview."""

from setlist_bot.application.ports import Store
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import Concert, Group, User
from setlist_bot.domain.rules import require

PAGE_SIZE = 20


class PersonalPerformances:
    def __init__(self, store: Store, user: User) -> None:
        self.store, self.user = store, user

    async def page(self, number: int) -> tuple[list[tuple[Concert, Group]], bool]:
        require(0 <= number <= 10000, "input")
        # RU: Лишняя строка показывает наличие следующей страницы без COUNT(*).
        # EN: Listing never selects a group, creates drafts or locks every membership.
        rows = await self.store.concerts_for_user(
            self.user.id, today(), offset=number * PAGE_SIZE, limit=PAGE_SIZE + 1
        )
        return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE
