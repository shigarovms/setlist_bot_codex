import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, MessageEntity, MessageOriginUser, Update, User
from test_telegram import FakeTelegram

from setlist_bot.transport.telegram import Processor, make_router, trace_message_route


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


def test_diagnostics_distinguish_command_entity_from_separate_bot_mention(caplog):
    caplog.set_level(logging.INFO, logger="setlist_bot.transport.telegram")
    head = "/song@botToPract696_bot"
    message = Message(
        message_id=42,
        date=datetime.now(UTC),
        chat=Chat(id=-9001, type="group"),
        from_user=User(id=9876543, is_bot=False, first_name="PRIVATE_NAME"),
        text=head + "\nPRIVATE_SONG  03:20",
        entities=[
            MessageEntity(type="bot_command", offset=0, length=5),
            MessageEntity(type="mention", offset=5, length=len(head) - 5),
        ],
    )
    trace_message_route(message, 79, 1110437563, "botToPract696_bot")
    assert "command_name=song" in caplog.text and "command_length=5" in caplog.text
    assert "entity_covers_head=False" in caplog.text and "address_matches=True" in caplog.text
    assert "PRIVATE_SONG" not in caplog.text and "PRIVATE_NAME" not in caplog.text


@pytest.mark.parametrize("forwarded", [True, False])
def test_private_diagnostics_only_inspect_explicitly_forwarded_messages(caplog, forwarded):
    caplog.set_level(logging.INFO, logger="setlist_bot.transport.telegram")
    stamp = datetime.now(UTC)
    user = User(id=9876543, is_bot=False, first_name="PRIVATE_NAME")
    text = "/song@botToPract696_bot\\\nPRIVATE_SONG  03:20"
    message = Message(
        message_id=42,
        date=stamp,
        chat=Chat(id=user.id, type="private"),
        from_user=user,
        text=text,
        entities=[MessageEntity(type="pre", offset=0, length=len(text))],
        forward_origin=MessageOriginUser(date=stamp, sender_user=user) if forwarded else None,
    )
    trace_message_route(message, 78, 1110437563, "botToPract696_bot")
    assert bool(caplog.records) == forwarded
    if forwarded:
        assert "code_format=True" in caplog.text and "command_entity=False" in caplog.text
        assert "trailing_backslash=True" in caplog.text and "address_matches=False" in caplog.text
    assert "PRIVATE_SONG" not in caplog.text and "PRIVATE_NAME" not in caplog.text
