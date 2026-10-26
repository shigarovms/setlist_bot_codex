import re

from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor

UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


async def test_normal_song_and_history_navigation_does_not_display_ids(engine, world):
    processor = Processor(engine)
    results = []
    results.append(
        await processor.process(
            1, 1, -300, "/group Новый коллектив", private=False, chat_admin=True
        )
    )
    catalog = await processor.process(2, 1, -100, "/catalog", private=False)
    results.append(catalog)
    card = await processor.process(3, 1, -100, catalog.buttons[0][1], private=False)
    results.append(card)
    hint = next(data for _, data in card.buttons if data.startswith("song_edit_menu "))
    results.append(await processor.process(4, 1, -100, hint, private=False))
    results.append(
        await processor.process(5, 1, -100, f"/use_concert {world.concert.id}", private=False)
    )
    picker = await processor.process(6, 1, -100, "/add Комета", private=False)
    results.append(picker)
    history = await processor.process(9, 1, -100, "/history", private=False)
    results.append(history)
    results.append(await processor.process(10, 1, -100, history.buttons[0][1], private=False))
    assert "Комета" in results[-1].text
    for response in results:
        assert not UUID_PATTERN.search(response.text)
        assert all(len(data.encode()) <= 64 for _, data in response.buttons)


async def test_edit_selected_song_without_identifiers_and_add_by_name(engine, world):
    processor = Processor(engine)
    await processor.process(1, 1, -100, f"/show_song {world.song.id}", private=False)
    edit = await processor.process(2, 1, -100, "/edit  Комета новая  05:00", private=False)
    saved = await processor.process(3, 1, -100, edit.buttons[0][1], private=False)
    assert "Комета новая" in saved.text and not UUID_PATTERN.search(saved.text)
    await processor.process(4, 1, -100, f"/use_concert {world.concert.id}", private=False)
    result = await processor.process(5, 1, -100, "/add Комета новая", private=False)
    assert "Комета новая" in result.text and "05:00" in result.text


async def test_published_draft_uses_buttons_and_applies_without_visible_ids(engine, world):
    processor = Processor(engine)
    await processor.process(1, 2, 2, f"/use_group {world.group.id}", private=True)
    await processor.process(2, 2, 2, f"/use_concert {world.concert.id}", private=True)
    await processor.process(3, 2, 2, "/add Комета", private=True)
    preview = await processor.process(4, 2, 2, "/publish", private=True)
    published = preview
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox))[0]
    assert not UUID_PATTERN.search(published.text + event.text)
    assert event.buttons and event.buttons[0][1].startswith("apply_submission ")
    result = await processor.process(6, 1, -100, event.buttons[0][1], private=False)
    assert "Комета" in result.text and not UUID_PATTERN.search(result.text)
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert len(snapshot["blocks"][0]["items"]) == 1
