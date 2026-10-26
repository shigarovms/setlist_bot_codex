from itertools import count

import pytest
from sqlalchemy import event

from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.concerts import parse_concert, today
from setlist_bot.domain.entities import Confirmation, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


@pytest.mark.parametrize("value", ["22 декабря", "22 дек", "22.12"])
def test_current_year_date_forms(value):
    parsed, title = parse_concert(value)
    assert (parsed.year, parsed.month, parsed.day) == (today().year, 12, 22) and not title


async def test_snapshot_batches_duplicate_song_reads(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"] * 100)
    queries = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith("SELECT") and "FROM songs" in statement:
            queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with PostgresUnitOfWork(engine) as uow:
            snapshot, _, _ = await Setlists(uow.store, world.admin).read(
                world.group.id, world.concert.id
            )
            assert len(snapshot["blocks"][0]["items"]) == 100
        assert len(queries) == 1
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_picker_timing_undo_and_publish_once(engine, world):
    numbers = count(1)

    async def send(text, raw=False):
        return await Processor(engine).process(
            next(numbers),
            1,
            1,
            text,
            private=True,
            is_callback=not text.startswith("/") and not raw,
        )

    await send(f"/use_group {world.group.id}")
    await send(f"/use_concert {world.concert.id}")
    chooser = await send("/suggestion")
    start = next(action for _, action in chooser.buttons if action.startswith("pick_start "))
    chosen = await send(start)
    label = next(label for row in chosen.reply_keyboard for label in row if "Комета" in label)
    assert label.startswith("03:38 ") and "1." not in label
    first = await send(label, True)
    assert "03:38" in first.text and "неизвестно" in first.text
    twice = await send(label, True)
    assert "07:21" in twice.text
    undo = await send("↩️ Удалить последнюю песню", True)
    assert "03:38" in undo.text and "Всего позиций: 1" in undo.text
    review = await send("✅ Завершить", True)
    card = await send(
        next(action for _, action in review.buttons if action.startswith("pick_confirm "))
    )
    publish = next(action for _, action in card.buttons if action.startswith("sldraft "))
    await send(publish)
    await send(publish)
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(Outbox)) == 1


async def test_force_reply_song_field_is_owner_bound_and_partial(engine, world):
    n = count(1)

    async def send(text, actor=1, reply=None):
        return await Processor(engine).process(
            next(n), actor, -100, text, private=False, reply_message_id=reply
        )

    menu = await send(f"song_edit_menu {world.song.id}")
    action = next(action for label, action in menu.buttons if action.endswith(" bpm"))
    prompt = await send(action)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(Confirmation, prompt.command_input_id)
        from setlist_bot.application.command_inputs import CommandInputs

        CommandInputs.write(row, {**CommandInputs.data(row), "prompt_id": 123})
    assert await send("140", actor=2, reply=123) is None
    preview = await send("140", reply=123)
    await send(preview.buttons[0][1])
    from setlist_bot.domain.entities import Song

    async with PostgresUnitOfWork(engine) as uow:
        song = await uow.store.get(Song, world.song.id)
        assert song.bpm == 140 and song.title == world.song.title
        assert song.lights == world.song.lights and song.duration_seconds == 218


def test_parallel_runs_cannot_share_the_same_test_database():
    from conftest import database_guard

    with database_guard("postgresql://fake/audit_guard_test"):
        with pytest.raises(RuntimeError, match="Another test run"):
            with database_guard("postgresql://fake/audit_guard_test"):
                raise AssertionError("parallel ownership must not be granted")
