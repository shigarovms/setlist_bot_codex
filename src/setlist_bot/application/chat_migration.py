"""Handle Telegram service migrations without granting anyone new permissions."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def migrate_binding(engine: AsyncEngine, old: int, new: int) -> None:
    async with engine.begin() as connection:
        # RU: Оба ID присылает Telegram, не пользовательская команда.
        # EN: Never expose this operation as a user-controlled callback.
        await connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": old})
        await connection.execute(
            text(
                "UPDATE chat_bindings SET chat_id=:new WHERE chat_id=:old "
                "AND NOT EXISTS (SELECT 1 FROM chat_bindings WHERE chat_id=:new)"
            ),
            {"old": old, "new": new},
        )
