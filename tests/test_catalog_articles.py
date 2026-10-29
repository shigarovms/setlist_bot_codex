from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramNotFound
from aiogram.methods import SendRichMessage
from aiogram.types import Chat, InputRichMessage, Message, RichMessage, Update
from aiogram.types import User as TelegramUser
from test_setlist_menus import sender
from test_telegram import FakeTelegram

from setlist_bot.application.catalog import Catalog
from setlist_bot.domain.entities import Confirmation, Song
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.article_import import rich_text
from setlist_bot.transport.articles import catalog_articles
from setlist_bot.transport.controller import Response
from setlist_bot.transport.telegram import Processor, make_router, send_response


class ArticleTelegram(FakeTelegram):
    def __init__(self, fail=None):
        super().__init__()
        self.rich_sent = []
        self.fail = fail

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendRichMessage):
            if self.fail:
                raise self.fail(method=method, message="synthetic API rejection")
            self.rich_sent.append(method)
            return Message(
                message_id=900 + len(self.rich_sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="supergroup"),
                from_user=TelegramUser(id=bot.id, is_bot=True, first_name="Test"),
                rich_message=RichMessage.model_validate(
                    method.rich_message.model_dump(exclude_none=True)
                ),
            )
        return await super().make_request(bot, method, timeout)


def songs(count):
    return [
        Song(group_id=uuid4(), title=f"Песня {i}", duration_seconds=60 + i) for i in range(count)
    ]


@pytest.mark.parametrize("language", ["ru", "en"])
def test_article_pages_preserve_all_fields_and_match_card_buttons(language):
    rows = songs(45)
    rows[0].title = '<b>Не HTML</b> & "песня"'
    rows[0].bpm, rows[0].lights, rows[0].comment = (
        120,
        "белый, красный",
        "KEY: Am\nДлинное примечание",
    )
    buttons = [(song.title, f"show_song {song.id}") for song in rows]
    pages = catalog_articles("Коллектив", rows, buttons, language)
    assert [len(page.buttons) for page in pages] == [20, 20, 5]
    assert [button for page in pages for button in page.buttons] == buttons
    values = []
    for page in pages:
        validated = InputRichMessage.model_validate({"blocks": page.blocks})
        assert validated.blocks[1].type == "table"
        cells = page.blocks[1]["cells"]
        assert all(cell["is_header"] for cell in cells[0])
        values.extend(rich_text(row[0]["text"]) for row in cells[1:])
    assert values == [song.title for song in rows]
    assert pages[0].blocks[1]["cells"][1][2]["text"] == "120"
    assert pages[0].blocks[1]["cells"][1][3]["text"] == "белый, красный"
    assert pages[0].blocks[1]["cells"][1][4]["text"] == rows[0].comment


def test_long_comments_split_pages_without_losing_or_truncating_cells():
    rows = songs(12)
    for song in rows:
        song.comment = "А" * 2000
    pages = catalog_articles("Группа", rows, [(song.title, "song") for song in rows], "ru")
    assert len(pages) > 1
    assert sum(len(page.blocks[1]["cells"]) - 1 for page in pages) == 12
    assert all(
        row[4]["text"] == "А" * 2000 for page in pages for row in page.blocks[1]["cells"][1:]
    )
    assert not catalog_articles("Группа", [], [], "ru")


@pytest.mark.parametrize("failure", [None, TelegramBadRequest, TelegramNotFound])
async def test_transport_article_and_rejection_fallback_keep_fields_buttons_and_cleanup(failure):
    row = songs(1)[0]
    pages = catalog_articles("Группа", [row], [(row.title, "show_song test")], "ru")
    response = Response("fallback", articles=pages, delete_message_ids=[10])
    session = ArticleTelegram(failure)
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        sent = await send_response(bot, -100, response)
    assert len(sent) == 1
    method = session.sent[0] if failure else session.rich_sent[0]
    if failure:
        assert method.reply_markup.inline_keyboard[0][0].callback_data == "show_song test"
        assert row.title in method.text and "01:00" in method.text
    else:
        assert method.reply_markup is None
        assert (
            method.rich_message.blocks[1].cells[1][0].text.button.callback_data == "show_song test"
        )
        assert method.rich_message.skip_entity_detection
        assert not session.sent
    assert session.deleted[0].message_id == 10


async def test_network_timeout_does_not_send_a_duplicate_text_fallback():
    row = songs(1)[0]
    response = Response("fallback", articles=catalog_articles("Группа", [row], [], "ru"))
    session = ArticleTelegram(TelegramNetworkError)
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        with pytest.raises(TelegramNetworkError):
            await send_response(bot, -100, response)
    assert not session.sent


async def test_catalog_search_archive_and_tenant_permissions_remain_unchanged(engine, world):
    send = sender(engine)
    card = await send("/catalog Комета")
    assert rich_text(card.articles[0].blocks[1]["cells"][1][0]["text"]) == "Комета"
    opened = await send(card.buttons[0][1])
    assert "KEY: Bbmin" in opened.text
    empty = await send("/catalog Неизвестная песня")
    assert not empty.articles and "Пока пусто" in empty.text
    denied = await send("/catalog", actor=3)
    assert not denied.articles and not denied.buttons
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).archive(world.group.id, world.song.id, 1, True)
    assert not (await send("/catalog")).articles
    archived = await send("/archived")
    assert rich_text(archived.articles[0].blocks[1]["cells"][1][0]["text"]) == "Комета"
    assert "Чужая песня" not in archived.text


async def test_router_sends_article_and_remembers_all_pages_for_existing_cleanup(engine, world):
    import json

    async with PostgresUnitOfWork(engine) as uow:
        for number in range(21):
            await Catalog(uow.store, world.admin).save(
                world.group.id, {"title": f"Дополнительная {number}", "duration_seconds": 60}
            )
    session = ArticleTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "test_bot"))
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=700001,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=-100, type="supergroup"),
                    from_user=TelegramUser(id=1, is_bot=False, first_name="Test"),
                    text="/catalog",
                ),
            ),
        )
    assert len(session.rich_sent) == 2 and not session.sent
    async with PostgresUnitOfWork(engine) as uow:
        panels = [json.loads(row.command) for row in await uow.store.find(Confirmation)]
        assert any(row.get("message_ids") == [901, 902] for row in panels)


@pytest.mark.parametrize("private", [False, True])
async def test_catalog_stays_and_song_card_is_reused_after_editing(engine, world, private):
    import json
    from itertools import count

    from setlist_bot.application.chat_cleanup import collect
    from setlist_bot.transport.telegram import delete_selection_message

    async with PostgresUnitOfWork(engine) as uow:
        other = await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Другая", "duration_seconds": 180}
        )
    processor, numbers = Processor(engine), count(800001)
    session = ArticleTelegram()
    chat_id = 1 if private else -100
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:

        async def send(command, *, source=None, reply=None, actor=1):
            response = await processor.process(
                next(numbers),
                actor,
                actor if private else chat_id,
                command,
                private=private,
                reply_message_id=reply,
                message_id=source,
                is_callback=not command.startswith("/") and reply is None,
            )
            sent = await send_response(bot, actor if private else chat_id, response)
            await processor.remember_response(actor if private else chat_id, response, sent)
            for identifier in response.delete_message_ids:
                await delete_selection_message(bot, actor if private else chat_id, identifier)
            return response, sent

        if private:
            await send(f"/use_group {world.group.id}")
        catalog, pages = await send("/catalog")
        catalog_ids = {message.message_id for message in pages}
        card, sent = await send(f"show_song {world.song.id}", source=pages[0].message_id)
        card_id = sent[0].message_id
        assert not card.edit_message_ids and not catalog_ids & set(card.delete_message_ids)
        cancelled, _ = await send("/cancel")
        assert not (catalog_ids | {card_id}) & set(cancelled.delete_message_ids)
        edit_action = next(
            action for _, action in card.buttons if action.startswith("song_edit_menu ")
        )
        editing, _ = await send(edit_action, source=card_id)
        assert editing.edit_message_ids == [card_id]
        bpm = next(action for _, action in editing.buttons if action.endswith(" bpm"))
        question, prompt = await send(bpm, source=card_id)
        preview, _ = await send("130", reply=prompt[0].message_id)
        saved, _ = await send(preview.buttons[0][1])
        assert saved.edit_message_ids == [card_id] and "130 BPM" in saved.text
        before = len(session.sent)
        next_card, _ = await send(f"show_song {other.id}", source=pages[0].message_id)
        assert next_card.edit_message_ids == [card_id] and "Другая" in next_card.text
        assert "Комета" not in next_card.text and len(session.sent) == before
        assert not catalog_ids & {method.message_id for method in session.deleted}
        if not private:
            member_card, member_sent = await send(
                f"show_song {world.song.id}", source=pages[0].message_id, actor=2
            )
            assert not member_card.edit_message_ids
            assert member_sent[0].message_id != card_id
            assert card_id not in member_card.delete_message_ids
        async with PostgresUnitOfWork(engine) as uow:
            panels = [
                json.loads(row.command)
                for row in await uow.store.find(
                    Confirmation, user_id=world.admin.id, chat_id=chat_id
                )
                if json.loads(row.command).get("kind") == "navigation_panel"
            ]
            assert len([panel for panel in panels if panel.get("slot") == "catalog"]) == 1
            assert len([panel for panel in panels if panel.get("slot") == "song"]) == 1
            identifiers = set(await collect(uow.store, chat_id))
            assert catalog_ids <= identifiers and card_id in identifiers


async def test_missing_song_card_is_replaced_without_deleting_catalog(engine, world):
    from aiogram.methods import EditMessageText

    class DeletedCardTelegram(ArticleTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, EditMessageText):
                raise TelegramBadRequest(method=method, message="message to edit not found")
            return await super().make_request(bot, method, timeout)

    processor = Processor(engine)
    session = DeletedCardTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        shown = await processor.process(810001, 1, -100, "/catalog", private=False)
        pages = await send_response(bot, -100, shown)
        await processor.remember_response(-100, shown, pages)
        first = await processor.process(
            810002, 1, -100, f"show_song {world.song.id}", private=False
        )
        first_sent = await send_response(bot, -100, first)
        await processor.remember_response(-100, first, first_sent)
        again = await processor.process(
            810003, 1, -100, f"show_song {world.song.id}", private=False
        )
        sent = await send_response(bot, -100, again)
        await processor.remember_response(-100, again, sent)
        assert sent[0].message_id != first_sent[0].message_id
        assert pages[0].message_id not in again.delete_message_ids


@pytest.mark.parametrize("expired", [False, True])
async def test_native_song_callback_handles_expired_ack_without_hiding_real_errors(
    engine, world, expired, caplog
):
    from aiogram.methods import AnswerCallbackQuery
    from aiogram.types import CallbackQuery

    class CallbackTelegram(ArticleTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, AnswerCallbackQuery):
                raise TelegramBadRequest(
                    method=method,
                    message="query is too old and response timeout expired"
                    if expired
                    else "PRIVATE_CAUSE",
                )
            return await super().make_request(bot, method, timeout)

    page = catalog_articles(
        world.group.name, [world.song], [(world.song.title, f"show_song {world.song.id}")], "ru"
    )[0]
    session = CallbackTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "test_bot"))
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=820001,
                callback_query=CallbackQuery(
                    id="expired",
                    chat_instance="test",
                    data=f"show_song {world.song.id}",
                    from_user=TelegramUser(id=1, is_bot=False, first_name="Test"),
                    message=Message(
                        message_id=901,
                        date=datetime.now(UTC),
                        chat=Chat(id=-100, type="supergroup"),
                        rich_message=RichMessage.model_validate({"blocks": page.blocks}),
                    ),
                ),
            ),
        )
    if expired:
        assert "Комета" in session.sent[-1].text
    else:
        assert "Комета" not in session.sent[-1].text
        assert "AnswerCallbackQuery" in caplog.text
        assert "PRIVATE_CAUSE" not in caplog.text
