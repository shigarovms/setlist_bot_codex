from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def test_catalog_change_invalidates_pending_approval(engine, world):
    processor = Processor(engine)
    await processor.process(1, 1, -100, f"/use_concert {world.concert.id}", private=False)
    await processor.process(2, 1, -100, f"/add {world.song.id}", private=False)
    preview = await processor.process(3, 1, -100, "/approve", private=False)
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).save(
            world.group.id,
            {
                "title": "Изменено после предпросмотра",
                "duration_seconds": 500,
            },
            world.song.id,
            1,
        )
    response = await processor.process(4, 1, -100, preview.buttons[0][1], private=False)
    assert "уже изменились" in response.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not (await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id))[
            2
        ]
