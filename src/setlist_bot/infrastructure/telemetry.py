"""In-process counters persisted in one small batch every 30 seconds; no message text."""

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.domain.entities import now
from setlist_bot.infrastructure.database import metadata

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, identity: dict[str, Any] | None = None) -> None:
        self.components: dict[str, dict[str, Any]] = {}
        self.identity = identity or {}

    def set(
        self,
        component: str,
        state: str,
        *,
        error: str = "",
        next_run: datetime | None = None,
        success: bool = False,
    ) -> None:
        old = self.components.get(component, {})
        self.components[component] = {
            "state": state,
            "last_activity_at": now(),
            "errors": old.get("errors", 0) + bool(error),
            "last_error": error or old.get("last_error", ""),
            "next_run": next_run,
            "last_success_at": now() if success else old.get("last_success_at"),
            "last_error_at": now() if error else old.get("last_error_at"),
            "details": self.identity if component == "bot" else old.get("details", {}),
        }

    async def flush(self, engine: AsyncEngine) -> None:
        table = metadata.tables["runtime_status"]
        async with engine.begin() as connection:
            for name, data in list(self.components.items()):
                statement = insert(table).values(
                    id=uuid4(), component=name, updated_at=now(), **data
                )
                await connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[table.c.component],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in (
                                "updated_at",
                                "last_activity_at",
                                "state",
                                "errors",
                                "last_error",
                                "next_run",
                                "last_success_at",
                                "last_error_at",
                                "details",
                            )
                        },
                    )
                )

    async def persist(self, engine: AsyncEngine) -> None:
        while True:
            try:
                self.set("bot", "process_running")
                await self.flush(engine)
            except Exception as exc:
                logger.warning("Runtime status write failed (%s)", type(exc).__name__)
            await asyncio.sleep(30)


class PollingStatusHandler(logging.Handler):
    """Record a fixed error code, never the log message or Telegram credentials."""

    def __init__(self, monitor: Monitor) -> None:
        super().__init__(level=logging.ERROR)
        self.monitor = monitor

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.monitor.set("telegram", "polling_error", error="TelegramPollingError")
