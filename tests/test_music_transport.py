import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import EditMessageText
from test_catalog_articles import ArticleTelegram
from test_music_search import ready
from test_setlist_menus import sender

from setlist_bot.domain.entities import MusicSearch, Outbox, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.telegram import Processor, send_response

TOKEN = "123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN"


async def test_article_checkbox_edits_same_message_with_table_and_navigation(engine, world):
    identifier = await ready(engine, world)
    response = await sender(engine)(f"mi_view {identifier.hex}", private=True)
    session = ArticleTelegram()
    async with Bot(TOKEN, session=session) as bot:
        messages = await send_response(bot, 1, response)
    assert [m.message_id for m in messages] == [700]
    assert len(session.edits) == 1 and not session.rich_sent and not session.sent
    edit = session.edits[0]
    assert edit.rich_message.blocks[1].cells[1][1].text.button.callback_data.startswith("mi_toggle")
    assert edit.reply_markup.inline_keyboard[-1][0].callback_data.startswith("mi_cancel")


@pytest.mark.parametrize("error", [TelegramNetworkError, TelegramRetryAfter])
async def test_ambiguous_or_rate_limited_article_edit_never_sends_an_extra_message(
    engine, world, error
):
    identifier = await ready(engine, world, 2)
    response = await sender(engine)(f"mi_view {identifier.hex}", private=True)

    class FailEdit(ArticleTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, EditMessageText):
                if error is TelegramRetryAfter:
                    raise error(method=method, message="synthetic limit", retry_after=10)
                raise error(method=method, message="synthetic timeout")
            return await super().make_request(bot, method, timeout)

    session = FailEdit()
    async with Bot(TOKEN, session=session) as bot:
        with pytest.raises(error):
            await send_response(bot, 1, response)
    assert not session.rich_sent and not session.sent


async def test_coalesced_updates_are_delivered_through_outbox_and_honor_retry_after(engine, world):
    identifier = await ready(engine, world, 3)
    send = sender(engine)
    await send(f"mi_toggle {identifier.hex} 0", private=True)
    await send(f"mi_toggle {identifier.hex} 1", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox, support_kind="music"))[0]
        event.next_attempt_at = now()
    session = ArticleTelegram()
    async with Bot(TOKEN, session=session) as bot:
        assert await deliver_one_outbox(bot, engine)
    assert len(session.edits) == 1 and not session.sent
    cells = session.edits[0].rich_message.blocks[1].cells
    assert cells[1][0].text.type == "strikethrough"
    assert cells[2][0].text.type == "strikethrough"
    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier)
        assert job.data["panel_ids"] == [700]


async def test_deleted_panel_is_replaced_and_new_article_id_is_remembered(engine, world):
    identifier = await ready(engine, world, 2)
    response = await sender(engine)(f"mi_view {identifier.hex}", private=True)

    class Missing(ArticleTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, EditMessageText):
                raise TelegramBadRequest(method=method, message="message to edit not found")
            return await super().make_request(bot, method, timeout)

    session = Missing()
    async with Bot(TOKEN, session=session) as bot:
        messages = await send_response(bot, 1, response)
    assert len(session.rich_sent) == 1 and not session.sent
    await Processor(engine).remember_response(1, response, messages)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(MusicSearch, identifier)).data["panel_ids"] == [901]


async def test_telegram_retry_after_survives_additional_selection_changes(engine, world):
    identifier = await ready(engine, world, 3)
    send = sender(engine)
    await send(f"mi_toggle {identifier.hex} 0", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox, support_kind="music"))[0]
        event.next_attempt_at = now()

    class Limited(ArticleTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, EditMessageText):
                raise TelegramRetryAfter(
                    method=method, message="synthetic rate limit", retry_after=15
                )
            return await super().make_request(bot, method, timeout)

    async with Bot(TOKEN, session=Limited()) as bot:
        with pytest.raises(TelegramRetryAfter):
            await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox, support_kind="music"))[0]
        retry_at = event.next_attempt_at
        assert retry_at > now() and event.last_error == "TelegramRetryAfter"
    await send(f"mi_toggle {identifier.hex} 1", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        events = await uow.store.find(Outbox, support_kind="music", sent=False)
        assert len(events) == 1 and events[0].next_attempt_at >= retry_at
