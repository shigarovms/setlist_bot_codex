from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, GetChatAdministrators, SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, ChatMemberOwner, Message, Update, User

from setlist_bot.domain.entities import Concert, Group, Membership, Song
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor, make_router


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[SendMessage] = []
        self.documents: list[SendDocument] = []
        self.admin_ids = [1]
        self.admin_requests = 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetChatAdministrators):
            self.admin_requests += 1
            return [
                ChatMemberOwner(
                    user=User(id=identifier, is_bot=False, first_name="Админ"), is_anonymous=False
                )
                for identifier in self.admin_ids
            ]
        if isinstance(method, SendMessage):
            self.sent.append(method)
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="supergroup"),
                text=method.text,
                from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
                entities=method.entities,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, SendDocument):
            self.documents.append(method)
            return Message(
                message_id=len(self.sent) + len(self.documents),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="supergroup"),
            )
        raise AssertionError(type(method))

    async def stream_content(self, url: str, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""


@pytest.mark.parametrize("table_fixture", ["repertoire.tsv", "repertoire-spaces.txt"])
async def test_aiogram_end_to_end_with_fake_telegram_and_real_postgres(engine, table_fixture):
    session = FakeTelegram()
    # Synthetic test token; FakeTelegram never performs network requests.
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "setlist_test_bot"))
        numbers = count(1)

        async def send(text, actor=1, callback=False):
            user = User(id=actor, is_bot=False, first_name="Админ" if actor == 1 else "Гитарист")
            message = Message(
                message_id=next(numbers),
                date=datetime.now(UTC),
                chat=Chat(id=-100, type="supergroup"),
                from_user=user,
                text=text,
            )
            if callback:
                update = Update(
                    update_id=next(numbers),
                    callback_query=CallbackQuery(
                        id=str(next(numbers)),
                        from_user=user,
                        chat_instance="chat",
                        message=message,
                        data=text,
                    ),
                )
            else:
                update = Update(update_id=next(numbers), message=message)
            await dispatcher.feed_update(bot, update)
            return session.sent[-1]

        assert "Создан" in (await send("/group Репетиционная группа")).text
        assert "ожидает" in (await send("/register гитарист", actor=2)).text
        pending = await send("/members")
        assert "Гитарист" in pending.text
        await send(pending.reply_markup.inline_keyboard[0][0].callback_data, callback=True)
        preview = await send("/song Комета | 03:38 | 110 | белый, красный")
        assert "Проверьте" in preview.text
        await send(preview.reply_markup.inline_keyboard[0][0].callback_data, callback=True)
        assert "рабочий" in (await send("/concert 2026-12-31 | Первый концерт")).text
        async with PostgresUnitOfWork(engine) as uow:
            song = (await uow.store.find(Song))[0]
            concert = (await uow.store.find(Concert))[0]
            assert len(await uow.store.find(Group)) == 1
            assert len(await uow.store.find(Membership, status="active")) == 2
        await send(f"/use_concert {concert.id}", actor=2)
        assert "Комета" in (await send(f"/add {song.id}", actor=2)).text
        assert "Подводка:" not in (await send("/add Комета", actor=2)).text
        assert "Средняя пауза: 00:30" in (await send("/pause 30", actor=2)).text
        assert "Подводка:" not in (await send("/pause 1 2 0", actor=2)).text
        assert "Подводка:" not in (await send("/pause 5", actor=2)).text
        assert "07:21" in (await send("/pause 1 2 default", actor=2)).text
        assert "права администратора" in (await send("/approve", actor=2)).text
        await send("/setlist")
        approve = await send("/approve")
        approved = await send(
            approve.reply_markup.inline_keyboard[0][0].callback_data, callback=True
        )
        assert "Утверждённый" in approved.text and "03:38" in approved.text
        previous = len(session.sent)
        await send("/help@another_bot")
        assert len(session.sent) == previous
        source = Path(__file__).with_name("fixtures").joinpath(table_fixture).read_text()
        imported = await send("/song@setlist_test_bot\n" + source)
        assert "новых — 9" in imported.text and "повторяющихся — 1" in imported.text
        saved = await send(imported.reply_markup.inline_keyboard[0][0].callback_data, callback=True)
        assert "Добавлено песен: 9" in saved.text
        async with PostgresUnitOfWork(engine) as uow:
            assert len(await uow.store.find(Song)) == 10
        reopen = await send("/reopen")
        await send(reopen.reply_markup.inline_keyboard[0][0].callback_data, callback=True)
        suggested = await send(
            "/suggestion@setlist_test_bot\nСлавик\nСердце лёд\nМеланхолия\nПодожгу\nАтомы\nVIBY"
        )
        assert "1. Славик" in suggested.text and "6. VIBY" in suggested.text
        assert any(
            row[0].text == "Админ · вариант 1" for row in suggested.reply_markup.inline_keyboard
        )
        await send("/setlist", actor=2)
        appended = await send("/add\nКомета\nРадуга", actor=2)
        assert "7. Комета" in appended.text and "8. Радуга" in appended.text


async def test_backup_is_sent_as_zip_document_through_telegram_transport(engine, world):
    import io
    import json
    from zipfile import ZipFile

    from aiogram.types import BufferedInputFile

    from setlist_bot.transport.telegram import send_response

    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        response = await Processor(engine).process(1, 1, -100, "/backup", private=False)
        await send_response(bot, -100, response)
    assert len(session.documents) == 1
    document = session.documents[0].document
    assert isinstance(document, BufferedInputFile)
    with ZipFile(io.BytesIO(document.data)) as archive:
        assert json.loads(archive.read("data.json"))["group"]["name"] == world.group.name


async def test_router_accepts_only_replies_to_its_working_messages_and_persists_prompt(
    engine, world
):
    from setlist_bot.application.setlists import Setlists

    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "setlist_test_bot"))
        ids = count(1)

        async def send(text, *, reply=None, callback=False, actor=1):
            user = User(id=actor, is_bot=False, first_name=f"Музыкант {actor}")
            message = Message(
                message_id=1000 + next(ids),
                date=datetime.now(UTC),
                chat=Chat(id=-100, type="supergroup"),
                from_user=user,
                text=text,
                reply_to_message=reply,
            )
            update_id = next(ids)
            if callback:
                update = Update(
                    update_id=update_id,
                    callback_query=CallbackQuery(
                        id=str(update_id),
                        from_user=user,
                        chat_instance="chat",
                        message=message,
                        data=text,
                    ),
                )
            else:
                update = Update(update_id=update_id, message=message)
            before = len(session.sent)
            await dispatcher.feed_update(bot, update)
            if len(session.sent) == before:
                return None
            sent = session.sent[-1]
            return Message(
                message_id=len(session.sent),
                date=datetime.now(UTC),
                chat=message.chat,
                from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
                text=sent.text,
            )

        working = await send(f"/use_concert {world.concert.id}")
        assert session.sent[-1].entities[0].type == "pre"
        assert session.sent[-1].parse_mode is None
        copy = session.sent[-1].reply_markup.inline_keyboard[0][0].callback_data
        prompt = await send(copy, callback=True, actor=2)
        assert session.sent[-1].reply_markup.force_reply
        assert "Какой вариант" in prompt.text
        assert await send("Просто обсуждение") is None
        unrelated = Message(
            message_id=9999,
            date=datetime.now(UTC),
            chat=working.chat,
            from_user=working.from_user,
            text="Other bot output",
        )
        assert await send("Комета", reply=unrelated) is None
        result = await send("Комета\nКомета", reply=prompt, actor=2)
        assert "Музыкант 2 · вариант 1" in str(session.sent[-1].reply_markup)
        foreign = working.model_copy(
            update={"from_user": User(id=987654, is_bot=True, first_name="Other")}
        )
        assert await send("Комета", reply=foreign) is None
        assert result.text.count("Комета") == 2
        async with PostgresUnitOfWork(engine) as uow:
            assert (await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id))[
                1
            ] == 1


async def test_monospace_chunks_preserve_literal_titles_utf16_entities_and_final_keyboard():
    from setlist_bot.transport.controller import Response
    from setlist_bot.transport.telegram import send_response

    text = ("1. <Песня> & `название` 🎸  03:43\n" * 200) + "Итого: 744:45"
    response = Response(text, [("Предложить свой вариант", "copy_list test")], monospace=True)
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        messages = await send_response(bot, -100, response)
    assert len(messages) > 1
    assert "".join(method.text for method in session.sent) == text
    for method in session.sent:
        size = len(method.text.encode("utf-16-le")) // 2
        assert size <= 3500 and method.entities[0].length == size
        assert method.entities[0].offset == 0 and method.entities[0].type == "pre"
        assert method.parse_mode is None
    assert all(method.reply_markup is None for method in session.sent[:-1])
    assert session.sent[-1].reply_markup.inline_keyboard[0][0].text == "Предложить свой вариант"


async def test_pdf_export_submenu_sends_both_a4_documents_through_aiogram(engine, world):
    from io import BytesIO

    from aiogram.types import BufferedInputFile
    from pypdf import PdfReader

    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "setlist_test_bot"))
        ids = count(1)

        async def send(text, callback=False):
            user = User(id=1, is_bot=False, first_name="Админ")
            message = Message(
                message_id=next(ids),
                date=datetime.now(UTC),
                chat=Chat(id=-100, type="supergroup"),
                from_user=user,
                text=text,
            )
            update_id = next(ids)
            update = (
                Update(
                    update_id=update_id,
                    callback_query=CallbackQuery(
                        id=str(update_id),
                        from_user=user,
                        chat_instance="chat",
                        message=message,
                        data=text,
                    ),
                )
                if callback
                else Update(update_id=update_id, message=message)
            )
            await dispatcher.feed_update(bot, update)
            return session.sent[-1]

        await send(f"/use_concert {world.concert.id}")
        shown = await send("/add Комета")
        button = next(
            row[0].callback_data
            for row in shown.reply_markup.inline_keyboard
            if row[0].text == "Экспорт"
        )
        menu = await send(button, callback=True)
        all_files = next(
            row[0].callback_data
            for row in menu.reply_markup.inline_keyboard
            if row[0].text == "Для всех · два PDF"
        )
        await send(all_files, callback=True)
    assert len(session.documents) == 2
    for event in session.documents:
        assert isinstance(event.document, BufferedInputFile)
        assert event.document.filename.endswith(".pdf")
        pdf = PdfReader(BytesIO(event.document.data))
        assert "Комета" in pdf.pages[0].extract_text()
        assert "ЧЕРНОВИК" in pdf.pages[0].extract_text()


async def test_group_creation_force_reply_binding_and_fresh_admin_checks_through_router(engine):
    session = FakeTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(Processor(engine), "setlist_test_bot"))
        updates = count(1)

        async def send(text, *, reply=None, callback=None, actor=1):
            user = User(id=actor, is_bot=False, first_name="Админ")
            uid = next(updates)
            message = Message(
                message_id=1000 + uid,
                date=datetime.now(UTC),
                chat=Chat(id=-300, type="supergroup"),
                from_user=user,
                text=text,
                reply_to_message=reply,
            )
            update = (
                Update(
                    update_id=uid,
                    callback_query=CallbackQuery(
                        id=str(uid),
                        from_user=user,
                        chat_instance="group",
                        message=callback,
                        data=text,
                    ),
                )
                if callback is not None
                else Update(update_id=uid, message=message)
            )
            before = len(session.sent)
            await dispatcher.feed_update(bot, update)
            if len(session.sent) == before:
                return None
            output = session.sent[-1]
            return Message(
                message_id=len(session.sent),
                date=datetime.now(UTC),
                chat=message.chat,
                from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
                text=output.text,
            )

        prompt = await send("/group@setlist_test_bot")
        assert session.sent[-1].reply_markup.force_reply
        assert await send("Посторонний текст") is None
        assert await send("Чужое название", actor=2, reply=prompt) is None
        prompt2 = await send("   ", reply=prompt)
        assert session.sent[-1].reply_markup.force_reply
        assert "от 1 до 200" in prompt2.text
        assert await send("Старый ответ", reply=prompt) is None
        preview = await send("Рок-группа «Атмосфера»", reply=prompt2)
        markup = session.sent[-1].reply_markup.inline_keyboard
        create = markup[0][0].callback_data
        edit = markup[1][0].callback_data
        session.admin_ids = []
        rejected = await send(create, callback=preview)
        assert "администратор" in rejected.text
        assert session.admin_requests == 2
        session.admin_ids = [1]
        renamed_prompt = await send(edit, callback=preview)
        assert session.sent[-1].reply_markup.force_reply
        renamed = await send("Атмосфера", reply=renamed_prompt)
        create_new = session.sent[-1].reply_markup.inline_keyboard[0][0].callback_data
        created = await send(create_new, callback=renamed)
        assert "Атмосфера" in created.text and session.admin_requests == 4
        duplicate = await send(create_new, callback=renamed)
        assert "устарел" in duplicate.text
        bound = await send("/group")
        assert bound.text == "В этом чате уже создан коллектив."
        assert session.sent[-1].reply_markup is None
