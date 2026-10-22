import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.rules import totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def test_migration_round_trip_and_schema_match(database_url, engine):
    environment = {**os.environ, "DATABASE_URL": database_url}
    for args in (("downgrade", "base"), ("upgrade", "head"), ("check",)):
        subprocess.run([sys.executable, "-m", "alembic", *args], env=environment, check=True)


async def test_upgrade_preserves_existing_outbox_messages(database_url):
    environment = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "eea0f8fa0e00"], env=environment, check=True
    )
    engine = create_async_engine(database_url)
    identifier = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO outbox (id,chat_id,text,sent) "
                    "VALUES (:id,-100,'Existing message',false)"
                ),
                {"id": identifier},
            )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )
        async with engine.connect() as connection:
            result = (
                await connection.execute(
                    text("SELECT text,buttons FROM outbox WHERE id=:id"), {"id": identifier}
                )
            ).one()
            assert result.text == "Existing message" and result.buttons == []
    finally:
        await engine.dispose()


async def test_pause_migration_preserves_legacy_timing_and_snapshots(database_url, engine, world):
    environment = {**os.environ, "DATABASE_URL": database_url}
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        for version in range(3):
            await service.edit(
                world.group.id, world.concert.id, version, "add", ["1", str(world.song.id)]
            )
        await service.edit(world.group.id, world.concert.id, 3, "pause", ["1", "1", "0"])
        await service.edit(world.group.id, world.concert.id, 4, "pause", ["1", "2", "30"])
        await service.approve(world.group.id, world.concert.id, 5)
        approved, _, _ = await service.read(world.group.id, world.concert.id)
        await service.edit(
            world.group.id, world.concert.id, 0, "default_pause", ["20"], private=True
        )
        await service.publish(world.group.id, world.concert.id)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "2f0007ae2d83"],
            env=environment,
            check=True,
        )
        async with engine.connect() as connection:
            historical = (await connection.execute(text("SELECT id,snapshot FROM revisions"))).all()
            drafts = (
                await connection.execute(text("SELECT id,content FROM personal_drafts"))
            ).all()
            proposals = (
                await connection.execute(text("SELECT id,snapshot FROM submissions"))
            ).all()
            assert totals(
                next(
                    d
                    for _, d in historical
                    if d["blocks"][0]["items"]
                    and len(d["blocks"][0]["items"]) == 3
                    and d["blocks"][0]["items"][0]["pause_after"] == 30
                )
            ) == totals(approved)
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )
        async with engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT id,snapshot FROM revisions"))
            ).all() == historical
            assert (
                await connection.execute(text("SELECT id,content FROM personal_drafts"))
            ).all() == drafts
            assert (
                await connection.execute(text("SELECT id,snapshot FROM submissions"))
            ).all() == proposals
            defaults = (
                (await connection.execute(text("SELECT default_pause_seconds FROM groups")))
                .scalars()
                .all()
            )
            assert defaults and set(defaults) == {5}
            old_defaults = (
                (await connection.execute(text("SELECT default_pause_seconds FROM setlists")))
                .scalars()
                .all()
            )
            assert old_defaults and set(old_defaults) == {0}
        async with PostgresUnitOfWork(engine) as uow:
            service = Setlists(uow.store, world.admin)
            assert totals((await service.read(world.group.id, world.concert.id))[0]) == totals(
                approved
            )
            await service.reopen(world.group.id, world.concert.id, 6)
            current, _, _ = await service.read(world.group.id, world.concert.id)
            assert current["default_pause_seconds"] == 0
            assert current["blocks"][0]["items"][0]["pause_before"] is None
            assert current["blocks"][0]["items"][1]["pause_before"] == 30
            assert totals(current) == totals(approved)
    finally:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )


async def test_downgrade_cannot_silently_drop_first_lead_in(database_url, engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.edit(world.group.id, world.concert.id, 0, "add", ["1", str(world.song.id)])
        await service.edit(world.group.id, world.concert.id, 1, "pause", ["1", "1", "30"])
        before = await service.read(world.group.id, world.concert.id)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "92c725b48a61"],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "Cannot downgrade" in result.stderr
    async with PostgresUnitOfWork(engine) as uow:
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
        ) == before


async def test_trash_upgrade_preserves_snapshots_and_recognizes_old_encore(
    database_url, engine, world
):
    from setlist_bot.domain.entities import Outbox, PersonalDraft, Submission

    environment = {**os.environ, "DATABASE_URL": database_url}
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        await service.edit(world.group.id, world.concert.id, 0, "encore", ["Бис"])
        await service.edit(world.group.id, world.concert.id, 1, "add", ["2", str(world.song.id)])
        snapshot, _, _ = await service.read(world.group.id, world.concert.id)
        submission = Submission(
            group_id=world.group.id,
            concert_id=world.concert.id,
            author_id=world.admin.id,
            snapshot=snapshot,
        )
        uow.store.add(submission)
        uow.store.add(
            Outbox(
                chat_id=-100,
                text="Existing proposal",
                group_id=world.group.id,
                concert_id=world.concert.id,
                buttons=[["open", f"view_submission {submission.id}"]],
            )
        )
        await service.read(world.group.id, world.concert.id, private=True)
        before = (await uow.store.find(PersonalDraft, owner_id=world.admin.id))[0].content
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "b73e5a190c24"], env=environment, check=True
    )
    try:
        async with engine.connect() as connection:
            snapshots = dict(
                (await connection.execute(text("SELECT id,snapshot FROM revisions"))).all()
            )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )
        async with engine.connect() as connection:
            assert (
                dict((await connection.execute(text("SELECT id,snapshot FROM revisions"))).all())
                == snapshots
            )
            assert (
                await connection.execute(text("SELECT kind FROM blocks WHERE title='Бис'"))
            ).scalar_one() == "encore"
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM groups WHERE deleted_at IS NOT NULL")
                )
            ).scalar_one() == 0
            event = (
                await connection.execute(text("SELECT group_id,concert_id,text FROM outbox"))
            ).one()
            assert event.group_id == world.group.id and event.concert_id == world.concert.id
            assert event.text == "Existing proposal"
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.find(PersonalDraft, owner_id=world.admin.id))[
                0
            ].content == before
    finally:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )


async def test_reply_migration_preserves_legacy_templates_and_queued_text(
    database_url, engine, world
):
    environment = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "3818f9aeed43"],
        env=environment,
        check=True,
    )
    template_id, outbox_id = uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO song_list_templates "
                    "(id,group_id,concert_id,chat_id,titles,expires_at,owner_id) "
                    "VALUES (:id,:group_id,:concert_id,-100,"
                    "'[\"Комета\"]',now()+interval '1 day',NULL)"
                ),
                {"id": template_id, "group_id": world.group.id, "concert_id": world.concert.id},
            )
            await connection.execute(
                text(
                    "INSERT INTO outbox (id,chat_id,text,sent,buttons) "
                    "VALUES (:id,-100,'Legacy <text>',false,'[]')"
                ),
                {"id": outbox_id},
            )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=environment,
            check=True,
        )
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT titles,version,group_epoch,concert_epoch "
                        "FROM song_list_templates WHERE id=:id"
                    ),
                    {"id": template_id},
                )
            ).one()
            assert row.titles == ["Комета"] and row.version is None
            assert row.group_epoch == row.concert_epoch == 0
            event = (
                await connection.execute(
                    text("SELECT text,monospace FROM outbox WHERE id=:id"), {"id": outbox_id}
                )
            ).one()
            assert event.text == "Legacy <text>" and event.monospace is False
    finally:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=environment,
            check=True,
        )
