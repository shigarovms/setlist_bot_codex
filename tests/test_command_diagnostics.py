import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, MessageEntity, Update, User
from test_telegram import FakeTelegram

from setlist_bot.transport.telegram import Processor, make_router


@pytest.mark.parametrize(
    "address,accepted",
    [("botToPract696_bot", True), ("BOTTOPRACT696_BOT", True), ("other_bot", False)],
)
async def test_diagnostics_identify_routing_without_logging_table_or_identity(
    caplog, address, accepted
):
    processor = AsyncMock(spec=Processor)
    processor.process.return_value = None
    session = FakeTelegram()
    caplog.set_level(logging.INFO, logger="setlist_bot.transport.telegram")
    async with Bot("1110437563:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(processor, "botToPract696_bot", diagnostics=True))
        head = "/song@" + address
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=77,
                message=Message(
                    message_id=42,
                    date=datetime.now(UTC),
                    chat=Chat(id=-9001, type="supergroup"),
                    from_user=User(id=9876543, is_bot=False, first_name="PRIVATE_NAME"),
                    text=head + "\nPRIVATE_SONG  03:20",
                    entities=[MessageEntity(type="bot_command", offset=0, length=len(head))],
                ),
            ),
        )
    assert processor.process.call_count == int(accepted)
    assert f"address_matches={accepted}" in caplog.text
    assert "command_entity=True" in caplog.text and "update=77" in caplog.text
    for private_value in ("PRIVATE_SONG", "PRIVATE_NAME", "9876543", "9001", "/song@", "03:20"):
        assert private_value not in caplog.text
    assert not session.sent and session.admin_requests == 0
