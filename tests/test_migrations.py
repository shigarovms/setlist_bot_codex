import os
import subprocess
import sys

from sqlalchemy import text


def alembic(database_url, *args, check=True):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=check,
    )


async def clear_synthetic_audit(engine):
    # RU: world создаёт современные события. Для проверки старой схемы удаляем
    # только эти синтетические записи; защиту отката журнала проверяем отдельно.
    # EN: Legacy migration fixtures predate audit, but preserve all actual domain rows.
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM admin_audit WHERE source='telegram'"))


async def test_migration_round_trip_and_schema_match(database_url, engine):
    for args in [("downgrade", "base"), ("upgrade", "head"), ("check",)]:
        alembic(database_url, *args)


async def test_new_migration_refuses_loss_of_event_details(database_url, engine, world):
    await clear_synthetic_audit(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE concerts SET city='Москва' WHERE id=:id"), {"id": world.concert.id}
        )
    result = alembic(database_url, "downgrade", "a6f8d31b2e90", check=False)
    assert result.returncode != 0 and "would lose data" in result.stderr
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT city FROM concerts WHERE id=:id"), {"id": world.concert.id}
            )
        ).scalar_one() == "Москва"


async def test_upgrade_backfills_new_optional_fields_without_touching_old_data(
    database_url, engine
):
    alembic(database_url, "downgrade", "a6f8d31b2e90")
    # RU/EN: Проверяем реальную старую схему / exercise the actual previous schema.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users(id,telegram_id,language,display_name) "
                "VALUES ('00000000-0000-0000-0000-000000000001',123,'ru','Test')"
            )
        )
    alembic(database_url, "upgrade", "head")
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT export_settings FROM users WHERE telegram_id=123")
            )
        ).scalar_one() == {}
    alembic(database_url, "check")


async def test_legacy_blocks_upgrade_preserves_actual_rows_and_time(database_url, engine, world):
    from uuid import uuid4

    from setlist_bot.application.setlists import Setlists
    from setlist_bot.domain.rules import totals
    from setlist_bot.infrastructure.database import PostgresUnitOfWork

    await clear_synthetic_audit(engine)
    alembic(database_url, "downgrade", "7c9e02d6a415")
    async with engine.begin() as connection:
        setlist_id = (
            await connection.execute(
                text("SELECT id FROM setlists WHERE concert_id=:id"), {"id": world.concert.id}
            )
        ).scalar_one()
        block_id = (
            await connection.execute(
                text("SELECT id FROM blocks WHERE setlist_id=:id"), {"id": setlist_id}
            )
        ).scalar_one()
        await connection.execute(
            text("UPDATE blocks SET break_seconds=90 WHERE id=:id"), {"id": block_id}
        )
        second = uuid4()
        await connection.execute(
            text(
                "INSERT INTO blocks(id,group_id,setlist_id,position,title,kind,break_seconds) "
                "VALUES (:id,:group,:list,2,'Бис','encore',0)"
            ),
            {"id": second, "group": world.group.id, "list": setlist_id},
        )
        for block, lead in ((block_id, 10), (second, None)):
            await connection.execute(
                text(
                    "INSERT INTO items(id,group_id,block_id,song_id,position,pause_before) "
                    "VALUES (:id,:group,:block,:song,1,:lead)"
                ),
                {
                    "id": uuid4(),
                    "group": world.group.id,
                    "block": block,
                    "song": world.song.id,
                    "lead": lead,
                },
            )
    alembic(database_url, "upgrade", "head")
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, _, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert len(snapshot["blocks"]) == 1 and totals(snapshot)[2] == 536
        assert [item["title"] for item in snapshot["blocks"][0]["items"]] == [
            "пауза",
            "Комета",
            "пауза",
            "Комета",
        ]


async def test_pause_downgrade_refuses_data_loss(database_url, engine, world):
    from setlist_bot.application.setlists import Setlists
    from setlist_bot.infrastructure.database import PostgresUnitOfWork

    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета", "2 мин"])
        before = await service.read(world.group.id, world.concert.id)
    await clear_synthetic_audit(engine)
    result = alembic(database_url, "downgrade", "7c9e02d6a415", check=False)
    assert result.returncode != 0 and "downgrade would lose data" in result.stderr
    async with PostgresUnitOfWork(engine) as uow:
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id) == before
        )
