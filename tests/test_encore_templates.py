from datetime import timedelta
from itertools import count

from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Outbox, SongListTemplate, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


def sender(engine):
    processor, ids = Processor(engine), count(1)

    async def send(command, actor=1, private=False, chat=-100):
        return await processor.process(
            next(ids),
            actor,
            actor if private else chat,
            command,
            private=private,
            display_name=f"Музыкант {actor}",
        )

    return send


def button(response, prefix):
    return next(data for _, data in response.buttons if data.startswith(prefix))


async def test_button_only_on_current_editable_setlist_and_empty_prompt(engine, world):
    send = sender(engine)
    empty = await send(f"/use_concert {world.concert.id}")
    assert empty.buttons[0][0] == "Предложить свой вариант"
    prompt = await send(button(empty, "copy_list "), actor=2)
    assert "Какой вариант вы предлагаете?" in prompt.text
    assert prompt.deliveries[0][1].force_reply and prompt.deliveries[0][1].suggestion_template_id
    shown = await send("/suggestion\nКомета\nКомета")
    prompt = await send(button(shown, "copy_list "), actor=2)
    assert prompt.deliveries[0][1].text.endswith("Комета\nКомета") and not prompt.buttons
    history = await send("/history")
    old_revision = next(data for label, data in history.buttons if "вариант 1" in label)
    old_card = await send(old_revision)
    assert not any(data.startswith("copy_list ") for _, data in old_card.buttons)
    approve = await send("/approve")
    assert not any(data.startswith("copy_list ") for _, data in approve.buttons)
    approved = await send(button(approve, "confirm "))
    assert not any(data.startswith("copy_list ") for _, data in approved.buttons)
    async with PostgresUnitOfWork(engine) as uow:
        _, version, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert version == 2  # Prompts never create revisions.


async def test_private_template_cannot_be_read_by_other_user_or_forwarded(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", private=True)
    await send(f"/use_concert {world.concert.id}", private=True)
    shown = await send("/suggestion\nКомета", private=True)
    copy = button(shown, "copy_list ")
    assert "Комета" in (await send(copy, private=True)).deliveries[0][1].text
    assert "не найден" in (await send(copy, actor=2, private=True)).text
    assert "не найден" in (await send(copy, actor=2)).text
    processor = Processor(engine)
    denied = await processor.process(999, 2, 1, copy, private=True)
    assert "не найден" in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Outbox)
        shared, version, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert version == 0 and not shared["blocks"][0]["items"]


async def test_published_proposals_and_previews_have_no_suggestion_button(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    await send("/suggestion\nКомета\nКомета", actor=2, private=True)
    preview = await send("/publish", actor=2, private=True)
    assert not any(data.startswith("copy_list ") for _, data in preview.buttons)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox))[0]
        assert event.monospace
    assert not any(data.startswith("copy_list ") for _, data in event.buttons)
    shown = await send(event.buttons[0][1])
    assert "Комета" in shown.text


async def test_shared_copy_in_private_chat_is_owned_and_buttons_expire(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    public = await send("/add Комета")
    assert "не найден" in (await send(button(public, "copy_list "), chat=-200)).text
    await send(f"/use_group {world.group.id}", private=True)
    await send(f"/use_concert {world.concert.id}", private=True)
    private_shared = await send("/shared", private=True)
    assert (
        "Комета"
        in (await send(button(private_shared, "copy_list "), private=True)).deliveries[0][1].text
    )
    async with PostgresUnitOfWork(engine) as uow:
        for template in await uow.store.find(SongListTemplate):
            template.expires_at = now() - timedelta(seconds=1)
    assert (
        "Срок действия кнопки истёк"
        in (await send(button(private_shared, "copy_list "), private=True)).text
    )


async def test_removed_encore_actions_do_not_change_working_list(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    for command in ("/encore", "/block Второй", "/break 1 30", "/remove_encore"):
        result = await send(command)
        assert "единый список" in result.text
