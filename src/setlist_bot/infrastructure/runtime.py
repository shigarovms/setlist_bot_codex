"""RU: Совместимость схемы и единственный polling. EN: Fail closed before Telegram polling."""

import asyncio
import contextlib
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from setlist_bot.domain.entities import DeploymentIdentity, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork

SCHEMA_REVISION = "c14f802d9a63"
LOCK_NAMESPACE = 1397509964


class SchemaMismatch(RuntimeError):
    pass


async def check_schema(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        versions: list[str] = list(
            (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars()
        )
    if versions != [SCHEMA_REVISION]:
        raise SchemaMismatch(
            f"Несовместимая схема БД: требуется {SCHEMA_REVISION}. "
            "Примените миграции соответствующей версии кода."
        )
    return SCHEMA_REVISION


def build_revision() -> str:
    """RU/EN: Local checkout or baked Docker revision; never execute a shell."""
    try:
        value = Path("/app/.build-revision").read_text().strip()
    except OSError:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, check=False
        )
        value = result.stdout.strip() if result.returncode == 0 else "unknown"
    return value[:80] or "unknown"


class BotInstance:
    """Hold a dedicated session lock for the entire polling lifetime.

    RU: Все экземпляры одного контура используют одну БД. Обрыв соединения с
    блокировкой завершает polling, вместо незаметного запуска второго экземпляра.
    EN: The watchdog never reconnects a lost lock session. Different databases do
    not share advisory locks: expected Telegram ID and deployment binding also matter.
    """

    def __init__(self, engine: AsyncEngine, bot_id: int, environment: str) -> None:
        self.engine, self.bot_id, self.environment = engine, bot_id, environment
        self.connection: AsyncConnection | None = None
        self.lock_id = (bot_id + 2**31) % 2**32 - 2**31

    async def __aenter__(self) -> "BotInstance":
        name = self.engine.url.database or ""
        if self.environment == "sandbox" and not name.endswith(("_sandbox", "_test")):
            raise RuntimeError("Тестовый бот может использовать только базу *_sandbox или *_test.")
        self.connection = await self.engine.connect()
        try:
            acquired = await self.connection.scalar(
                text("SELECT pg_try_advisory_lock(:namespace, :bot)"),
                {"namespace": LOCK_NAMESPACE, "bot": self.lock_id},
            )
            await self.connection.commit()
            if not acquired:
                raise RuntimeError(
                    "В этом контуре уже работает экземпляр бота; второй polling запрещён."
                )
            async with PostgresUnitOfWork(self.engine) as uow:
                await uow.store.lock_chat(LOCK_NAMESPACE)
                identities = await uow.store.find(DeploymentIdentity, key="main")
                if identities:
                    identity = identities[0]
                    if identity.bot_id != self.bot_id or identity.environment != self.environment:
                        raise RuntimeError(
                            "База привязана к другому боту или контуру. Запуск запрещён."
                        )
                else:
                    uow.store.add(
                        DeploymentIdentity(bot_id=self.bot_id, environment=self.environment)
                    )
            return self
        except BaseException:
            await self._close()
            raise

    async def watch(self) -> None:
        while True:
            await asyncio.sleep(5)
            await self.heartbeat()

    async def heartbeat(self) -> None:
        # RU/EN: Never reconnect a session after losing its advisory lock.
        if self.connection is None or self.connection.closed or self.connection.invalidated:
            raise RuntimeError("Потеряно соединение, удерживающее блокировку экземпляра.")
        await asyncio.wait_for(self.connection.execute(text("SELECT 1")), timeout=8)
        await self.connection.commit()

    async def _close(self) -> None:
        if self.connection is not None:
            try:
                if not self.connection.invalidated:
                    await self.connection.execute(
                        text("SELECT pg_advisory_unlock(:namespace, :bot)"),
                        {"namespace": LOCK_NAMESPACE, "bot": self.lock_id},
                    )
                    await self.connection.commit()
            finally:
                await self.connection.close()
                self.connection = None

    async def __aexit__(self, *args: Any) -> None:
        with contextlib.suppress(Exception):
            await self._close()


def identity_details(bot_id: int, username: str, environment: str, location: str) -> dict[str, Any]:
    return {
        "bot_id": bot_id,
        "bot_username": username,
        "environment": environment,
        "location": location,
        "revision": build_revision(),
        "started_at": now().isoformat(),
        "instance_id": uuid4().hex,
        "schema": SCHEMA_REVISION,
    }
