"""RU: Новые меню и подбор без живого Telegram. EN: Isolated database regression checks."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from test_migrations import alembic
from test_setlist_menus import action, sender

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.recommendations import suggested_titles
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.concerts import today
from setlist_bot.domain.entities import Concert, Submission
from setlist_bot.domain.rules import AppError, editable_content, pause_item
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.i18n import MESSAGES
from setlist_bot.transport.render import render_setlist


async def test_empty_menu_and_back_navigation_bind_original_group(engine, world):
    send = sender(engine)
    empty = await send(f"/use_concert {world.concert.id}", remember=50)
    assert [label for label, _ in empty.buttons] == [
        "Предложить свой вариант",
        "⚙️ Опции",
        "◀️ Назад к выступлениям",
    ]
    # RU/EN: Даже поддельное старое действие не утверждает пустоту / backend still rejects it.
    denied = await send(f"slact {empty.menu_card_id.hex} approve", source=50)
    assert denied.callback_notice
    await send(f"/use_group {world.other.id}")
    returned = await send(action(empty, "◀️ Назад к выступлениям"), source=50)
    assert any(str(world.concert.id) in callback for _, callback in returned.buttons)
    assert not any(str(world.other_concert.id) in callback for _, callback in returned.buttons)
    await send(f"/use_concert {world.concert.id}")
    populated = await send("/suggestion Комета")
    assert [label for label, _ in populated.buttons][-2:] == [
        "✅ Утвердить",
        "◀️ Назад к выступлениям",
    ]


async def test_private_publish_only_when_different_and_edit_first(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", private=True)
    initial = await send(f"/use_concert {world.concert.id}", private=True)
    assert [label for label, _ in initial.buttons] == [
        "Изменить",
        "⚙️ Опции",
        "◀️ Назад к выступлениям",
    ]
    changed = await send("/suggestion\nКомета\n2 мин\nКомета", private=True)
    assert [label for label, _ in changed.buttons] == [
        "Изменить",
        "Предложить этот вариант",
        "⚙️ Опции",
        "◀️ Назад к выступлениям",
    ]
    publish = action(changed, "Предложить этот вариант")
    # Shared changes can make an old personal publish button redundant.
    await send(f"/use_concert {world.concert.id}")
    await send("/suggestion\nКомета\n2 мин\nКомета")
    rejected = await send(publish, private=True)
    assert rejected.callback_notice and "совпадает" in rejected.callback_notice
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Submission)
    same = await send("/setlist", private=True)
    assert "Предложить этот вариант" not in dict(same.buttons)


async def test_settings_title_type_order_clear_cancel_and_back(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", private=True)
    await send(f"/use_concert {world.concert.id}", private=True)
    panel = await send(f"concert_settings {world.concert.id}", private=True)
    assert [label for label, _ in panel.buttons][:4] == [
        "Изменить: Название",
        "Задать: Длительность выступления",
        "Изменить: Тип выступления",
        "Задать: Город",
    ]
    title = await send(action(panel, "Изменить: Название"), private=True, remember=501)
    assert title.force_reply and "Сейчас: Новый год" in title.text
    renamed = await send("Сольный вечер", private=True, reply=501)
    assert "Сольный вечер" in renamed.text
    types = await send(action(renamed, "Изменить: Тип выступления"), private=True)
    selected = await send(action(types, "Сольник"), private=True)
    assert "Тип выступления: Сольник" in selected.text
    old = await send(action(types, "Фест"), private=True)
    assert "устарел" in old.text
    types = await send(action(selected, "Изменить: Тип выступления"), private=True)
    custom = await send(action(types, "Свой вариант"), private=True, remember=502)
    assert custom.force_reply and "Сейчас: Сольник" in custom.text
    edited = await send("Акустика", private=True, reply=502)
    shown = await send("/setlist", private=True)
    assert shown.text.splitlines()[0] == "Коллектив А (Акустика)"
    types = await send(action(edited, "Изменить: Тип выступления"), private=True)
    await send(action(types, "Отмена"), private=True)
    title = await send(action(edited, "Изменить: Название"), private=True, remember=503)
    cleared = await send("-", private=True, reply=503)
    options = await send(action(cleared, "◀️ Назад"), private=True)
    assert "Параметры выступления" in dict(options.buttons)
    assert "Удалить выступление" not in dict(options.buttons)
    async with PostgresUnitOfWork(engine) as uow:
        concert = await uow.store.get(Concert, world.concert.id)
        assert concert.title == "" and concert.performance_type == "Акустика"


async def test_type_choice_permissions_conflict_and_original_concert(engine, world):
    send = sender(engine)
    denied = await send(f"concert_settings {world.concert.id}", actor=2)
    assert "администратор" in denied.text
    panel = await send(f"concert_settings {world.concert.id}")
    chooser = await send(action(panel, "Изменить: Тип выступления"))
    await send(f"/use_concert {world.concert2.id}")
    selected = await send(action(chooser, "Фест"))
    assert "Новый год" in selected.text
    chooser = await send(action(selected, "Изменить: Тип выступления"))
    # Another field edit invalidates the old choice by metadata version.
    await send("/concert_city Москва")
    stale = await send(action(chooser, "Корпорат"))
    assert "измен" in stale.text.lower()
    async with PostgresUnitOfWork(engine) as uow:
        concert = await uow.store.get(Concert, world.concert.id)
        assert concert.performance_type == "Фест"
        assert (await uow.store.get(Concert, world.concert2.id)).performance_type == ""
        service = ConcertDetails(uow.store, world.admin)
        for field, value in (("concert_type", "a" * 201), ("concert_title", "A\nB")):
            with pytest.raises(AppError, match="input"):
                await service.update(world.group.id, world.concert.id, field, value)


async def make_past(uow, world, days, seconds, kind="", *, approved=True, group=None):
    group_id = group or world.group.id
    title = f"Песня {days} {seconds}"
    await Catalog(uow.store, world.admin).save(
        group_id, {"title": title, "duration_seconds": seconds}
    )
    service = Setlists(uow.store, world.admin)
    concert = await service.create(group_id, today() - timedelta(days=days))
    concert.performance_type = kind
    await service.edit_songs(group_id, concert.id, 0, [title])
    if approved:
        await service.approve(group_id, concert.id, 1)
    return concert, title


@pytest.mark.parametrize(
    "kind, same_seconds, expected",
    [
        ("", 650, "closest"),
        ("Сольник", 650, "same"),
        ("Сольник", 721, "closest"),
        ("Сольник", 720, "same"),
    ],
)
async def test_recommendation_nearest_duration_with_bounded_type_preference(
    engine, world, kind, same_seconds, expected
):
    async with PostgresUnitOfWork(engine) as uow:
        closest, close_title = await make_past(uow, world, 20, 601, "Фест")
        same, same_title = await make_past(uow, world, 30, same_seconds, "Сольник")
        await make_past(uow, world, 1, 700, "Корпорат")
        await make_past(uow, world, 2, 600, "Сольник", approved=False)
        await make_past(uow, world, 3, 600, "Сольник", group=world.other.id)
        target = await uow.store.get(Concert, world.concert.id)
        target.performance_duration_seconds, target.performance_type = 600, kind
        result = await suggested_titles(uow.store, world.admin, target)
        assert result.titles == [same_title if expected == "same" else close_title]
        assert result.source_date == (same if expected == "same" else closest).date
        assert result.duration_seconds == (same_seconds if expected == "same" else 601)
        assert not set(result.titles) & set(result.new + result.popular)


async def test_recommendation_no_duration_latest_and_group_copy_heading(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await make_past(uow, world, 20, 600, "Сольник")
        last, title = await make_past(uow, world, 2, 650, "Фест")
        target = await uow.store.get(Concert, world.concert.id)
        target.performance_type = "Сольник"
        result = await suggested_titles(uow.store, world.admin, target)
        assert result.source_date == last.date and result.titles == [title]
    send = sender(engine)
    panel = await send(f"/use_concert {world.concert.id}")
    prompt = await send(action(panel, "Предложить свой вариант"))
    assert "в личном чате" in prompt.text.lower() and "кнопками" in prompt.text
    copied = prompt.deliveries[0][1]
    assert copied.text.startswith("На выступлении ")
    assert "— 10:50" in copied.text and copied.monospace_body == title
    assert copied.text.endswith("\n\n" + title) and copied.force_reply


def test_narrow_render_wraps_title_not_number_or_duration():
    from datetime import date
    from uuid import uuid4

    title = "Оченьдлинноеназваниебезпробелов и продолжение песни"
    items = [
        {"song_id": "1", "title": title, "duration_seconds": 240, "pause_before": None}
    ] * 10 + [pause_item(120)]
    snapshot = {"default_pause_seconds": 5, "blocks": [{"items": items, "break_seconds": 0}]}
    rendered = render_setlist(snapshot, Concert(group_id=uuid4(), date=date(2027, 1, 1)), "ru", 3)
    rows = rendered.splitlines()
    start = next(i for i, line in enumerate(rows) if line.startswith("01."))
    assert "04:00" in rows[start] and rows[start + 1].startswith("    ")
    assert all(len(line) <= 27 for line in rows[start:])
    assert "10." in rendered and "xx. пауза" in rendered
    assert rows[start].index("04:00") == rows[-1].index("42:45")
    assert "песни" in rendered
    assert editable_content(snapshot) == editable_content({**snapshot, "approved_by": "Admin"})


async def test_performance_type_migration_preserves_old_rows_and_guards_downgrade(
    database_url, engine, world
):
    # Only this dedicated *_test database is migrated; the running sandbox is untouched.
    alembic(database_url, "downgrade", "c14f802d9a63")
    alembic(database_url, "upgrade", "head")
    async with engine.begin() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM concerts")) == 3
        assert (
            await connection.scalar(text("SELECT count(*) FROM concerts WHERE performance_type=''"))
            == 3
        )
        await connection.execute(text("UPDATE concerts SET performance_type='Сольник'"))
    result = alembic(database_url, "downgrade", "c14f802d9a63", check=False)
    assert result.returncode != 0 and "would lose data" in result.stderr
    alembic(database_url, "check")


def test_ru_public_messages_use_performance_terminology():
    assert not any(
        "мероприяти" in value.lower() or "концерт" in value.lower()
        for value in MESSAGES["ru"].values()
    )
