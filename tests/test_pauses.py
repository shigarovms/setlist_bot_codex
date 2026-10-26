import pytest

from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.rules import AppError, parse_pause, totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("5 мин", 300), ("2:30", 150), ("1 час 13 минут 43 секунды", 4423), ("3 мин 30 сек", 210)],
)
def test_human_pause_input(value, seconds):
    assert parse_pause(value) == seconds


async def test_pause_rows_duplicates_and_private_cancel_are_atomic(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        await service.edit_songs(
            world.group.id,
            world.concert.id,
            0,
            ["Комета", "2:30", "Комета"],
            private=True,
            replace=True,
        )
        private, version, _ = await service.read(world.group.id, world.concert.id, private=True)
        assert totals(private)[2] == 218 * 2 + 150
        shared, _, _ = await service.read(world.group.id, world.concert.id)
        assert not shared["blocks"][0]["items"]
        with pytest.raises(AppError):
            await service.edit_songs(
                world.group.id,
                world.concert.id,
                version,
                ["Комета", "Никогда не существовавшая 7293"],
                private=True,
                replace=True,
            )
        assert (await service.read(world.group.id, world.concert.id, private=True))[0] == private
