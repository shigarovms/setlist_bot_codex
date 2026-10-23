from datetime import timedelta
from itertools import count

from menu_helpers import options

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
    assert prompt.force_reply and prompt.suggestion_template_id
    shown = await send("/suggestion\nКомета\nКомета")
    prompt = await send(button(shown, "copy_list "), actor=2)
    assert prompt.text.endswith("Комета\nКомета") and not prompt.buttons
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
    assert "Комета" in (await send(copy, private=True)).text
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
    await send(button(preview, "confirm "), actor=2, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox))[0]
        assert event.monospace
    assert not any(data.startswith("copy_list ") for _, data in event.buttons)
    shown = await send(event.buttons[0][1])
    assert not any(data.startswith("copy_list ") for _, data in shown.buttons)


async def test_shared_copy_in_private_chat_is_owned_and_buttons_expire(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    public = await send("/add Комета")
    assert "не найден" in (await send(button(public, "copy_list "), chat=-200)).text
    await send(f"/use_group {world.group.id}", private=True)
    await send(f"/use_concert {world.concert.id}", private=True)
    private_shared = await send("/shared", private=True)
    assert "Комета" in (await send(button(private_shared, "copy_list "), private=True)).text
    async with PostgresUnitOfWork(engine) as uow:
        for template in await uow.store.find(SongListTemplate):
            template.expires_at = now() - timedelta(seconds=1)
    assert (
        "Срок действия кнопки истёк"
        in (await send(button(private_shared, "copy_list "), private=True)).text
    )


async def test_add_remove_encore_songs_and_drop_encore_preserve_history(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}", actor=2)
    await send("/add Комета", actor=2)
    encore = await send("/encore", actor=2)
    menu = await options(send, encore, actor=2)
    assert "Добавить песни в бис" in dict(menu.buttons)
    picker = await send(
        next(data for label, data in menu.buttons if label == "Добавить песни в бис"), actor=2
    )
    preview = await send(button(picker, "offer_add "), actor=2)
    await send(button(preview, "confirm "), actor=2)
    shown = await send("/add_encore\nКомета\nКомета", actor=2)
    assert "2.3. Комета" in shown.text
    assert (await send(button(shown, "copy_list "), actor=2)).text.endswith(
        "\n".join(["Комета"] * 4)
    )
    remove = await send("/remove_encore_song", actor=2)
    confirm = await send(button(remove, "encore_pick "), actor=2)
    assert "Выбранная песня" in confirm.text
    changed = await send(button(confirm, "confirm "), actor=2)
    assert "2.2. Комета" in changed.text and "2.3. Комета" not in changed.text
    drop = await send("/remove_encore", actor=2)
    result = await send(button(drop, "confirm "), actor=2)
    assert "1. Комета" in result.text and "Бис" not in result.text
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        current, _, _ = await service.read(world.group.id, world.concert.id)
        assert len(current["blocks"]) == 1
        assert current["blocks"][0]["break_seconds"] == 0
        history = await service.history(world.group.id, world.concert.id)
        assert history[0].action == "remove_block"
        assert any(
            len(r.snapshot["blocks"]) == 2 and len(r.snapshot["blocks"][1]["items"]) == 3
            for r in history
        )


async def test_encore_changes_are_private_and_shared_approval_is_enforced(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    await send("/encore", actor=2, private=True)
    await send("/add_encore Комета", actor=2, private=True)
    drop = await send("/remove_encore", actor=2, private=True)
    await send(button(drop, "confirm "), actor=2, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        shared, version, _ = await service.read(world.group.id, world.concert.id)
        assert version == 0 and len(shared["blocks"]) == 1
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
        await service.approve(world.group.id, world.concert.id, 1)
    await send(f"/use_concert {world.concert.id}", actor=2)
    assert "утверждён" in (await send("/encore", actor=2)).text
    assert "утверждён" in (await send("/add_encore Комета", actor=2)).text


async def test_stale_encore_removal_does_not_remove_newer_songs(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    await send("/encore")
    await send("/add_encore Комета")
    preview = await send("/remove_encore")
    await send("/add_encore Комета")
    stale = await send(button(preview, "confirm "))
    assert "уже изменились" in stale.text and "2.2. Комета" in stale.text


async def test_duplicate_encore_is_rejected_and_options_target_the_original_concert(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    assert "ещё не добавлен" in (await send("/add_encore Комета")).text
    encore = await send("/encore")
    assert "уже добавлен" in (await send("/encore")).text
    menu = await options(send, encore)
    old_button = next(data for label, data in menu.buttons if label == "Добавить песни в бис")
    await send(f"/use_concert {world.concert2.id}")
    assert "Выберите песню для биса" in (await send(old_button)).text
