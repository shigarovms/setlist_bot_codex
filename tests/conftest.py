import fcntl
import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import User
from setlist_bot.infrastructure.database import PostgresUnitOfWork, metadata


@contextmanager
def database_guard(url):
    # RU: Каждый тест очищает общую _test-базу. Второй pytest должен остановиться,
    # а не менять схему под первым процессом. Секреты URL не попадают в имя файла.
    # EN: Tests truncate shared tables. Refuse a concurrent pytest run rather than
    # migrating underneath it; hash the URL so credentials never appear in a path.
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"setlist-pytest-{digest}.lock"
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another test run owns this database") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://setlist:setlist@localhost:5432/setlist_test"
    )
    if not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("Tests truncate tables: use a dedicated database ending in _test")
    with database_guard(url):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env={**os.environ, "DATABASE_URL": url},
            check=True,
        )
        yield url


@pytest_asyncio.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        tables = ", ".join(f'"{name}"' for name in metadata.tables)
        await connection.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def world(engine: AsyncEngine) -> SimpleNamespace:
    from datetime import date

    async with PostgresUnitOfWork(engine) as uow:
        admin, member, stranger = User(telegram_id=1), User(telegram_id=2), User(telegram_id=3)
        for user in (admin, member, stranger):
            uow.store.add(user)
        await uow.store.flush()
        group = await Groups(uow.store, admin).create("Коллектив А", -100)
        other = await Groups(uow.store, admin).create("Коллектив Б", -200)
        membership = await Groups(uow.store, member).register(group.id, "гитарист")
        await uow.store.flush()
        await Groups(uow.store, admin).decide_member(group.id, membership.id, True)
        song = await Catalog(uow.store, admin).save(
            group.id,
            {
                "title": "Комета",
                "duration_seconds": 218,
                "bpm": 110,
                "lights": "белый, красный",
                "comment": "KEY: Bbmin",
            },
        )
        other_song = await Catalog(uow.store, admin).save(
            other.id,
            {
                "title": "Чужая песня",
                "duration_seconds": 300,
            },
        )
        concert = await Setlists(uow.store, admin).create(group.id, date(2026, 12, 31), "Новый год")
        concert2 = await Setlists(uow.store, admin).create(
            group.id, date(2027, 1, 1), "Второй концерт"
        )
        other_concert = await Setlists(uow.store, admin).create(other.id, date(2027, 1, 2))
    return SimpleNamespace(
        admin=admin,
        member=member,
        stranger=stranger,
        group=group,
        other=other,
        song=song,
        other_song=other_song,
        concert=concert,
        concert2=concert2,
        other_concert=other_concert,
    )
