from datetime import date, timedelta

import pytest
from test_setlist_menus import sender

from setlist_bot.domain.concerts import button_date, date_label
from setlist_bot.domain.entities import Concert, Context, Group, Membership, PersonalDraft
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.commands import MENUS


@pytest.fixture(autouse=True)
def fixed_calendar(monkeypatch):
    for module in (
        "setlist_bot.domain.concerts",
        "setlist_bot.application.performances",
        "setlist_bot.transport.controller",
    ):
        monkeypatch.setattr(module + ".today", lambda: date(2026, 10, 30))


def test_numeric_button_dates_do_not_change_human_setlist_dates():
    assert button_date(date(2026, 7, 26)) == "26.07"
    assert button_date(date(2002, 3, 13)) == "13.03.2002"
    assert button_date(date(2027, 1, 1)) == "01.01.2027"
    assert date_label(date(2026, 7, 26)) == "26 июля"
    assert date_label(date(2002, 3, 13)) == "2002 год 13 марта"
    assert "all_my_concerts" in MENUS["private"]
    assert "all_my_concerts" not in MENUS["group"]


async def test_concerts_and_past_buttons_use_numeric_dates_and_keep_other_fields(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        first = await uow.store.get(Concert, world.concert.id)
        first.city = "Москва"
        first.performance_duration_seconds = 6000
        second = await uow.store.get(Concert, world.concert2.id)
        second.date = date(2002, 3, 13)
    send = sender(engine)
    future = await send("/concerts")
    label = next(label for label, action in future.buttons if str(world.concert.id) in action)
    assert label == "31.12 · Новый год · Москва · 1ч 40мин"
    past = await send("/past_concerts")
    assert next(label for label, action in past.buttons if str(world.concert2.id) in action) == (
        "13.03.2002 · Второй концерт"
    )
    opened = await send(f"/use_concert {world.concert.id}")
    assert "31 декабря" in opened.text


async def test_all_groups_need_no_selected_group_and_private_list_does_not_create_drafts(
    engine, world
):
    async with PostgresUnitOfWork(engine) as uow:
        concert = await uow.store.get(Concert, world.other_concert.id)
        concert.date, concert.title = date(2026, 11, 17), "Фестиваль"
    send = sender(engine)
    result = await send("/all_my_concerts", private=True)
    assert [label for label, _ in result.buttons] == [
        "17.11 Коллектив Б Фестиваль",
        "31.12 Коллектив А Новый год",
        "01.01.2027 Коллектив А Второй концерт",
    ]
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Context, user_id=world.admin.id, chat_id=1)
        assert not await uow.store.find(PersonalDraft, owner_id=world.admin.id)
    opened = await send(result.buttons[0][1], private=True)
    assert "Коллектив Б" in opened.text and "Фестиваль" in opened.text
    async with PostgresUnitOfWork(engine) as uow:
        context = (await uow.store.find(Context, user_id=world.admin.id, chat_id=1))[0]
        assert context.group_id == world.other.id
        assert context.concert_id == world.other_concert.id


async def test_cross_group_list_does_not_leak_into_chats_or_to_nonmembers(engine, world):
    send = sender(engine)
    member = await send("/all_my_concerts", private=True, actor=2)
    assert len(member.buttons) == 2
    assert "Коллектив Б" not in str(member)
    stranger = await send("/all_my_concerts", private=True, actor=3)
    assert not stranger.buttons
    grouped = await send("/all_my_concerts", actor=2)
    assert "Коллектив Б" not in grouped.text
    assert all(action.startswith("https://t.me/") for _, action in grouped.buttons)
    assert grouped.buttons[0][1].endswith("?start=all_my_concerts")
    deep_link = await send("/start all_my_concerts", private=True, actor=2)
    assert deep_link.buttons == member.buttons
    forged = await send(f"my_concert {world.other_concert.id.hex}", private=True, actor=2)
    assert "Коллектив Б" not in forged.text and not forged.buttons
    public_callback = await send(f"my_concert {world.concert.id.hex}")
    assert "личном чате" in public_callback.text


async def test_trash_and_membership_changes_invalidate_list_and_old_selection(engine, world):
    send = sender(engine)
    first = await send("/all_my_concerts", private=True, actor=2)
    old_button = first.buttons[0][1]
    async with PostgresUnitOfWork(engine) as uow:
        member = (await uow.store.find(Membership, user_id=world.member.id))[0]
        member.status = "pending"
        group = await uow.store.get(Group, world.other.id)
        from setlist_bot.domain.entities import now

        group.deleted_at = now()
        concert = await uow.store.get(Concert, world.concert2.id)
        concert.deleted_at = now()
    denied = await send(old_button, private=True, actor=2)
    assert not denied.menu_card_id
    assert not (await send("/all_my_concerts", private=True, actor=2)).buttons
    remaining = await send("/all_my_concerts", private=True)
    assert len(remaining.buttons) == 1
    assert "Новый год" in remaining.buttons[0][0]


async def test_pagination_is_stable_and_past_events_follow_upcoming(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        for index in range(22):
            uow.store.add(
                Concert(group_id=world.group.id, date=date(2026, 11, 1) + timedelta(days=index))
            )
        past = await uow.store.get(Concert, world.other_concert.id)
        past.date = date(2002, 3, 13)
    send = sender(engine)
    first = await send("/all_my_concerts", private=True)
    following = next(action for _, action in first.buttons if action == "all_my_concerts 1")
    second = await send(following, private=True)
    first_ids = {action for _, action in first.buttons if action.startswith("my_concert ")}
    second_ids = {action for _, action in second.buttons if action.startswith("my_concert ")}
    assert len(first_ids) == 20 and len(second_ids) == 5 and first_ids.isdisjoint(second_ids)
    assert second.buttons[-2][0].startswith("13.03.2002 Коллектив Б")
    assert (await send("all_my_concerts 0", private=True)).buttons == first.buttons
    assert not (await send("/all_my_concerts -1", private=True)).buttons
