from datetime import date

import pytest
from test_setlist_menus import sender

from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.concerts import parse_concert
from setlist_bot.domain.entities import Concert
from setlist_bot.infrastructure.database import PostgresUnitOfWork


@pytest.fixture(autouse=True)
def calendar_today(monkeypatch):
    monkeypatch.setattr("setlist_bot.domain.concerts.today", lambda: date(2026, 11, 1))


@pytest.mark.parametrize(
    "value,expected",
    [
        ("18.03", date(2027, 3, 18)),
        ("18 марта", date(2027, 3, 18)),
        ("01.11", date(2026, 11, 1)),
        ("29 февраля", date(2028, 2, 29)),
        ("18.03.2002", date(2002, 3, 18)),
        ("2025/08/21", date(2025, 8, 21)),
    ],
)
def test_next_occurrence_and_explicit_year(value, expected):
    assert parse_concert(value) == (expected, "")


async def test_creation_year_date_move_confirmation_conflict_and_no_setlist_loss(engine, world):
    send = sender(engine)
    preview = await send("/concert 18.03 Вечер")
    assert "18 марта 2027" in preview.text
    await send(f"/use_concert {world.concert.id}")
    await send("/suggestion Комета")
    async with PostgresUnitOfWork(engine) as uow:
        before = await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
    question = await send("/concert_date 18.03")
    assert "18 марта 2027" in question.text
    confirm = next(action for _, action in question.buttons if action.startswith("confirm "))
    await send(confirm)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Concert, world.concert.id)).date == date(2027, 3, 18)
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id) == before
        )
    question = await send("/concert_date 19.03")
    confirm = next(action for _, action in question.buttons if action.startswith("confirm "))
    async with PostgresUnitOfWork(engine) as uow:
        await ConcertDetails(uow.store, world.admin).update(
            world.group.id, world.concert.id, "concert_city", "Москва"
        )
    conflict = await send(confirm)
    assert "конфликт" in conflict.text.lower() or "измен" in conflict.text.lower()


async def test_concerts_names_and_multi_group_switch(engine, world):
    send = sender(engine)
    many = await send("/concerts")
    assert many.text == "Выступления Коллектив А:"
    assert many.buttons[0][1] == "past_concerts"
    assert not any(action == "groups" for _, action in many.buttons)
    await send(f"/use_group {world.group.id}", private=True)
    personal = await send("/concerts", private=True)
    assert personal.buttons[0] == ("Мои коллективы", "groups")
    assert personal.buttons[1][1] == "past_concerts"
    single = await send("/concerts", actor=2)
    assert single.buttons[0][1] == "past_concerts"


@pytest.mark.parametrize("private", [False, True])
async def test_back_to_performances_only_offers_group_switch_in_private(engine, world, private):
    from test_setlist_menus import action

    send = sender(engine)
    if private:
        await send(f"/use_group {world.group.id}", private=True)
    card = await send(f"/use_concert {world.concert.id}", private=private, remember=70)
    returned = await send(action(card, "◀️ Назад к выступлениям"), private=private, source=70)
    assert returned.text == "Выступления Коллектив А:"
    assert any(callback == "groups" for _, callback in returned.buttons) is private
