from itertools import count

from setlist_bot.domain.entities import CalendarSubscription
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


def sender(engine, base="https://calendar.example.org"):
    processor = Processor(engine, bot_username="botToPract696_bot", calendar_public_base_url=base)
    updates = count(1)

    async def send(command, *, actor=1, private=True):
        return await processor.process(
            next(updates), actor, actor if private else -100, command, private=private
        )

    return send


async def test_calendar_ui_issues_link_only_in_private_and_confirms_rotation_cancel_disable(
    engine, world
):
    send = sender(engine)
    menu = await send("/calendar")
    assert "cal_scope all" in dict(menu.buttons).values()
    assert any(world.group.name == label for label, _ in menu.buttons)
    created = await send("cal_create all")
    assert created.monospace_body.startswith("https://calendar.example.org/calendar/")
    assert "Пароль" not in created.text
    repeated = await send("cal_create all")
    assert repeated.monospace_body is None and "перевыпустите" in repeated.text
    rotate_action = next(action for _, action in repeated.buttons if action.endswith("rotate"))
    confirmation = await send(rotate_action)
    cancel = next(action for _, action in confirmation.buttons if action.startswith("cal_cancel "))
    await send(cancel)
    assert (await send("cal_create all")).monospace_body is None
    confirmation = await send(rotate_action)
    yes = next(action for _, action in confirmation.buttons if action.startswith("cal_confirm "))
    assert (await send(yes, actor=2)).monospace_body is None
    rotated = await send(yes)
    assert rotated.monospace_body != created.monospace_body
    assert (await send(yes)).monospace_body is None
    enabled = await send("cal_scope all")
    disable = next(action for _, action in enabled.buttons if action.endswith("disable"))
    confirmation = await send(disable)
    await send(
        next(action for _, action in confirmation.buttons if action.startswith("cal_confirm "))
    )
    async with PostgresUnitOfWork(engine) as uow:
        row = (await uow.store.find(CalendarSubscription))[0]
        assert not row.enabled
        assert rotated.monospace_body not in str(vars(row))
    public = await send("/calendar", private=False)
    assert not public.monospace_body
    assert public.buttons[0][1] == "https://t.me/botToPract696_bot?start=calendar"
    assert "cal_scope all" in dict((await send("/start calendar")).buttons).values()


async def test_subscriptions_can_be_created_by_members_and_deep_link_rechecks_access(engine, world):
    send = sender(engine)
    created = await send(f"cal_create {world.group.id.hex}", actor=2)
    assert created.monospace_body
    forbidden = await send(f"cal_create {world.other.id.hex}", actor=2)
    assert forbidden.monospace_body is None
    opened = await send(f"/start performance_{world.concert.id.hex}", actor=2)
    assert "Новый год" in opened.text
    denied = await send(f"/start performance_{world.other_concert.id.hex}", actor=2)
    assert world.other.name not in denied.text
    mine = await send("/all_my_concerts")
    assert ("📅 Подключить календарь", "calendar") in mine.buttons


async def test_missing_public_configuration_does_not_issue_unusable_links(engine, world):
    send = sender(engine, "")
    response = await send("/calendar")
    assert "пока не подключены" in response.text and not response.monospace_body
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(CalendarSubscription)
