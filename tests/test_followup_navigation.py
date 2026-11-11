from aiogram import Bot
from test_setlist_menus import sender
from test_telegram import FakeTelegram

from setlist_bot.domain.entities import Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox


async def test_selected_group_opens_its_performances_before_optional_transfer(engine, world):
    send = sender(engine)
    selected = await send(f"/use_group {world.other.id}", private=True)
    assert selected.buttons[0] == (
        "Выступления Коллектив Б",
        f"group_concerts {world.other.id.hex}",
    )
    assert selected.buttons[1] == ("Репертуар Коллектив Б", f"group_catalog {world.other.id.hex}")
    assert selected.buttons[2] == ("Составы Коллектив Б", f"sf_team {world.other.id.hex}")
    assert selected.buttons[3][1].startswith("ct_begin ")
    assert selected.buttons[-1] == ("◀️ Все мои коллективы", "groups")
    opened = await send(selected.buttons[0][1], private=True)
    assert any(str(world.other_concert.id) in action for _, action in opened.buttons)
    member = await send(f"/use_group {world.group.id}", actor=2, private=True)
    assert [label for label, _ in member.buttons] == [
        "Выступления Коллектив А",
        "Репертуар Коллектив А",
        "Выйти из коллектива",
        "◀️ Все мои коллективы",
    ]


async def test_deletion_retires_shared_and_private_setlist_buttons_without_resending_text(
    engine, world
):
    send = sender(engine)
    public = await send(f"/use_concert {world.concert.id}", remember=100)
    await send(f"/use_group {world.group.id}", private=True)
    await send(f"/use_concert {world.concert.id}", private=True, remember=200)
    question = await send("/delete_concert")
    confirm = next(action for _, action in question.buttons if action.startswith("confirm "))
    deleted = await send(confirm)
    assert [label for label, _ in deleted.buttons] == [
        "Связаться с поддержкой",
        "◀️ К выступлениям",
    ]
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Outbox, support_kind="retire", sent=False)) == 2
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        for _ in range(10):
            if not await deliver_one_outbox(bot, engine):
                break
    assert {request.message_id for request in session.markups} == {100, 200}
    assert not session.sent and not session.edits
    for request in session.markups:
        assert [row[0].text for row in request.reply_markup.inline_keyboard] == [
            "Связаться с поддержкой",
            "◀️ К выступлениям",
        ]
    stale = await send(public.buttons[0][1], source=100)
    assert not stale.suggestion_template_id
    remaining = await send("/concerts")
    assert not any(str(world.concert.id) in action for _, action in remaining.buttons)
