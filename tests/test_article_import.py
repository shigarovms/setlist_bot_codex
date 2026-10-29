from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, RichMessage, Update
from aiogram.types import User as TelegramUser
from test_catalog_articles import ArticleTelegram
from test_setlist_menus import sender

from setlist_bot.domain.entities import CatalogImport, CatalogProposal, Song
from setlist_bot.domain.importing import parse_import
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.article_import import article_heading, article_table_source
from setlist_bot.transport.articles import catalog_articles
from setlist_bot.transport.commands import MENUS
from setlist_bot.transport.i18n import MESSAGES
from setlist_bot.transport.telegram import Processor, make_router


def article(rows, command="", **cell_options):
    blocks = [{"type": "paragraph", "text": command}] if command else []
    blocks.append(
        {
            "type": "table",
            "cells": [
                [{"text": value, "align": "left", "valign": "top", **cell_options} for value in row]
                for row in rows
            ],
        }
    )
    return {"blocks": blocks}


def test_rich_cells_and_catalog_round_trip_preserve_columns_and_literal_text():
    data = article(
        [
            ["Название", "Время", "Свет", "BPM", "Комментарий"],
            [
                ["Песня ", {"type": "bold", "text": "«А, Б»"}],
                "03:20",
                "белый, красный",
                "103",
                'KEY: Am\n"тише"',
            ],
            ["Пустые поля", "00:05", "", "", ""],
        ],
        "/add_new_songs@botToPract696_bot",
    )
    source = article_table_source(data)
    parsed = parse_import(source)
    assert article_heading(data).startswith("/add_new_songs@")
    assert not parsed.error and not any(row.error for row in parsed.rows)
    assert parsed.rows[0].data == {
        "title": "Песня «А, Б»",
        "duration_seconds": 200,
        "lights": "белый, красный",
        "bpm": 103,
        "comment": 'KEY: Am\n"тише"',
    }
    assert parsed.rows[1].data["bpm"] is None
    song = Song(group_id=uuid4(), title="Комета", duration_seconds=218)
    page = catalog_articles("Группа", [song], [(song.title, f"show_song {song.id}")], "ru")[0]
    restored = parse_import(article_table_source({"blocks": page.blocks}))
    assert restored.rows[0].data["title"] == "Комета"
    assert restored.rows[0].data["bpm"] is None
    assert restored.rows[0].data["lights"] == ""


@pytest.mark.parametrize(
    "data, code",
    [
        (article([["A", "03:20"]], colspan=2), "article_merged_cells"),
        (article([["A", "03:20"]], rowspan=2), "article_merged_cells"),
        ({"blocks": []}, "article_table_required"),
        ({"blocks": article([["A", "03:20"]])["blocks"] * 2}, "article_one_table"),
        (article([["x"] * 51]), "import_size"),
        (article([["x"]] * 502), "import_size"),
        (article([["x" * 100001]]), "import_size"),
    ],
)
def test_unsupported_or_excessive_article_fails_without_silent_data_loss(data, code):
    with pytest.raises(AppError, match=code):
        article_table_source(data)


@pytest.mark.parametrize("private", [True, False])
async def test_one_song_and_text_table_use_same_field_order(engine, world, private):
    send = sender(engine)
    if private:
        await send(f"/use_group {world.group.id}", private=True)
    single = await send(
        "/add_new_songs Новая  03:20  белый, красный  103  KEY: Am", private=private
    )
    assert "103 BPM" in single.text and "белый, красный" in single.text
    await send(single.buttons[0][1], private=private)
    table = await send(
        "/add_new_songs\nВторая  03:20  белый, красный  103  KEY: Am", private=private
    )
    assert "новых — 1" in table.text
    await send(table.buttons[0][1], private=private)
    async with PostgresUnitOfWork(engine) as uow:
        first = (await uow.store.find(Song, title="Новая"))[0]
        second = (await uow.store.find(Song, title="Вторая"))[0]
        assert first.bpm == second.bpm == 103
        assert first.lights == second.lights == "белый, красный"
        assert first.comment == second.comment == "KEY: Am"


async def test_member_single_proposal_bulk_import_permissions_and_hidden_aliases(engine, world):
    send = sender(engine)
    preview = await send("/add_new_songs Новая  03:20  красный  103", actor=2)
    await send(preview.buttons[0][1], actor=2)
    denied = await send("/add_new_songs\nНовая\t03:20", actor=2)
    assert "администратор" in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(CatalogProposal)) == 1
        assert not await uow.store.find(Song, title="Новая")
        assert not await uow.store.find(CatalogImport)
    assert "новых — 1" in (await send("/import\nТаблица\t03:20")).text
    assert "03:20" in (await send("/song Старая команда  03:20  103  красный")).text
    for menu in ("private", "group", "admin"):
        assert "add_new_songs" in MENUS[menu]
        assert "song" not in MENUS[menu] and "import" not in MENUS[menu]
    for language in ("ru", "en"):
        help_text = MESSAGES[language]["help"]
        assert help_text.index("/add_new_songs") < help_text.index("/catalog")
        assert "/song " not in help_text and "/import " not in help_text


@pytest.mark.parametrize("private", [True, False])
async def test_article_reply_is_bound_to_prompt_not_later_group_and_double_confirm_is_safe(
    engine, world, private
):
    send = sender(engine)
    if private:
        await send(f"/use_group {world.group.id}", private=True)
    prompt = await send("/add_new_songs", private=private, remember=101)
    assert prompt.force_reply
    if private:
        await send(f"/use_group {world.other.id}", private=True)
    processor = Processor(engine)
    data = article([["/delete_group", "03:20", "белый, красный", "103", "комментарий"]])
    imported = await processor.process(
        9001,
        1,
        1 if private else -100,
        "",
        private=private,
        reply_message_id=101,
        message_id=102,
        article=data,
    )
    assert "новых — 1" in imported.text and "Удалить коллектив" not in imported.text
    callback = imported.buttons[0][1]
    saved = await send(callback, private=private)
    assert "Добавлено песен: 1" in saved.text
    repeated = await send(callback, private=private)
    assert "Добавлено песен" not in repeated.text
    async with PostgresUnitOfWork(engine) as uow:
        songs = await uow.store.find(Song, title="/delete_group")
        assert len(songs) == 1 and songs[0].group_id == world.group.id


async def test_article_refuses_unrelated_prompt_and_revoked_admin_rights(engine, world):
    send = sender(engine)
    await send("/concert", remember=111)
    processor = Processor(engine)
    data = article([["Новая", "03:20"]])
    wrong = await processor.process(
        9101, 1, -100, "", private=False, reply_message_id=111, article=data
    )
    assert "/add_new_songs" in wrong.text
    await send("/add_new_songs", remember=112)
    async with PostgresUnitOfWork(engine) as uow:
        from setlist_bot.domain.entities import Membership

        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.admin.id)
        )[0]
        member.role = "member"
    denied = await processor.process(
        9102, 1, -100, "", private=False, reply_message_id=112, article=data
    )
    assert "администратор" in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(CatalogImport)
        assert not await uow.store.find(Song, title="Новая")


async def test_router_accepts_addressed_article_and_native_table_callback(engine, world):
    session = ArticleTelegram()
    ids = count(100001)
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "test_bot"))
        user = TelegramUser(id=1, is_bot=False, first_name="Test")

        async def send(data=None, text=None, callback=None, actor=user):
            message = Message(
                message_id=next(ids),
                date=datetime.now(UTC),
                chat=Chat(id=-100, type="supergroup"),
                from_user=actor,
                text=text,
                rich_message=RichMessage.model_validate(data) if data else None,
            )
            update = (
                Update(update_id=next(ids), message=message)
                if callback is None
                else Update(
                    update_id=next(ids),
                    callback_query=CallbackQuery(
                        id=str(next(ids)),
                        from_user=actor,
                        message=message,
                        chat_instance="test",
                        data=callback,
                    ),
                )
            )
            await dispatcher.feed_update(bot, update)

        data = article([["Новая", "03:20", "красный", "103"]], "/add_new_songs@another_bot")
        await send(data)
        assert not session.sent
        data["blocks"][0]["text"] = "/add_new_songs@test_bot"
        await send(data)
        assert "новых — 1" in session.sent[-1].text
        await send(text="/catalog")
        method = session.rich_sent[-1]
        assert method.reply_markup is None
        title_button = method.rich_message.blocks[1].cells[1][0].text.button
        assert title_button.style == "link"
        await send(
            {"blocks": method.rich_message.model_dump(exclude_none=True)["blocks"]},
            callback=title_button.callback_data,
        )
        assert "Комета" in session.sent[-1].text and "KEY: Bbmin" in session.sent[-1].text
        count_before = len(session.sent)
        await send(article([["Посторонняя", "03:20"]]))
        assert len(session.sent) == count_before
