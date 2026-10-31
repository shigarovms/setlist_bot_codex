from datetime import date

import pytest
from test_setlist_menus import sender

from setlist_bot.application.groups import Groups
from setlist_bot.domain.entities import Concert
from setlist_bot.domain.performance_input import parse_performance
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("setlist_bot.domain.concerts.today", lambda: date(2026, 11, 1))


@pytest.mark.parametrize(
    "text,title,seconds,kind,city,info",
    [
        (
            "23.04 Клуб Космос. 45 мин. Сольник. Москва. Играем во время выноса торта, под плюс",
            "Клуб Космос",
            2700,
            "Сольник",
            "Москва",
            "Играем во время выноса торта, под плюс",
        ),
        (
            "25.12. Арарат арена  Ереван  Корпорат  31мин",
            "Арарат арена",
            1860,
            "Корпорат",
            "Ереван",
            "",
        ),
        (
            "15дек Летний парк  Питер  корп  1:25:00",
            "Летний парк",
            5100,
            "Корпорат",
            "Санкт-Петербург",
            "",
        ),
        (
            "15 дек 2028  Питер  заказняк  1:25  Летний парк",
            "Летний парк",
            5100,
            "Корпорат",
            "Санкт-Петербург",
            "",
        ),
        ("2028/08/21 Парк  фестиваль  г. Москва  45", "Парк", 2700, "Фест", "Москва", ""),
        (
            "23.04 Название: Москва. город: Неизвестный город. тип: Свой тип. длительность: 10 мин",
            "Москва",
            600,
            "Свой тип",
            "Неизвестный город",
            "",
        ),
    ],
)
def test_recognizes_examples_and_field_order(text, title, seconds, kind, city, info):
    result = parse_performance(text)
    assert (result.title, result.duration, result.performance_type, result.city, result.info) == (
        title,
        seconds,
        kind,
        city,
        info,
    )


def test_unknown_notes_are_preserved_and_duplicate_fields_require_correction():
    result = parse_performance("15дек Клуб им. Виктора Цоя. Ереван. комментарий. Ещё подробности")
    assert result.title == "Клуб им. Виктора Цоя"
    assert result.info == "комментарий. Ещё подробности"
    with pytest.raises(AppError, match="concert_fields_conflict"):
        parse_performance("15дек Парк  30 мин  40 мин")
    with pytest.raises(AppError):
        parse_performance("31 февраля Клуб")


async def test_preview_then_atomic_creation_edit_and_replay(engine, world):
    send = sender(engine)
    preview = await send("/concert 23.04 Клуб Космос. 45мин. концерт. Москва. Под плюс")
    assert "23 апреля 2027" in preview.text
    assert "45мин" in preview.text and "Сольник" in preview.text and "Под плюс" in preview.text
    confirm = next(a for _, a in preview.buttons if a.startswith("confirm "))
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Concert, title="Клуб Космос")
    created = await send(confirm)
    assert "Клуб Космос" in created.text
    await send(confirm)
    async with PostgresUnitOfWork(engine) as uow:
        rows = await uow.store.find(Concert, title="Клуб Космос")
        assert len(rows) == 1 and rows[0].performance_duration_seconds == 2700
        assert rows[0].city == "Москва" and rows[0].extra_info == "Под плюс"
    preview = await send("/concert 15дек Парк  корп  31мин")
    changed = await send(next(a for _, a in preview.buttons if a.startswith("concert_correct ")))
    assert changed.force_reply and "15дек Летний парк  Питер  корп  1:25:00" in changed.text
    assert not (
        await send(next(a for _, a in preview.buttons if a.startswith("confirm ")))
    ).text.startswith("Выступление создано")


async def test_article_is_the_default_for_new_groups(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        group = await Groups(uow.store, world.admin).create("Новый коллектив", -300)
        assert group.setlist_style == "article"
