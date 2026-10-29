import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendRichMessage
from aiogram.types import User as TelegramUser
from test_catalog_articles import ArticleTelegram
from test_collective_workflows import action, client

from setlist_bot.application.music_search import notify_search
from setlist_bot.domain.entities import MusicSearch, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox


async def test_confirm_default_name_manual_input_and_cancel_are_owner_bound(engine, world):
    send = client(engine)
    question = await send(f"mi_open {world.group.id.hex}", remember=70)
    assert "Искать коллектив «Коллектив А»?" == question.text
    assert len(question.buttons) == 3
    yes = action(question, "mi_name_yes")
    denied = await send(yes, actor=2)
    assert "20–30" not in denied.text
    started = await send(yes)
    assert "20–30" in started.text
    assert "20–30" not in (await send(yes)).text
    async with PostgresUnitOfWork(engine) as uow:
        job = (await uow.store.find(MusicSearch))[0]
        assert job.query == world.group.name and job.data["origin_chat_id"] == -100
        assert job.chat_id == world.admin.telegram_id
    await send("/cancel")
    question = await send(f"mi_open {world.group.id.hex}")
    prompt = await send(action(question, "mi_name_manual"), remember=71)
    assert prompt.force_reply
    queued = await send("Другой исполнитель", reply=71)
    assert "20–30" in queued.text
    await send("/cancel")
    question = await send(f"mi_open {world.group.id.hex}")
    assert "не запущен" in (await send(action(question, "mi_name_cancel"))).text


@pytest.mark.parametrize("blocked", [False, True])
async def test_ready_results_go_to_requester_with_truthful_group_notice_and_private_fallback(
    engine, world, blocked
):
    send = client(engine)
    question = await send(f"mi_open {world.group.id.hex}")
    await send(action(question, "mi_name_yes"))
    async with PostgresUnitOfWork(engine) as uow:
        job = (await uow.store.find(MusicSearch))[0]
        identifier = job.id
        job.status = "choosing"
        job.data = {
            **job.data,
            "tracks": [{"title": "Комета", "seconds": 218, "bpm": None}],
            "artist": {"id": "123", "name": "Artist"},
            "panel_ids": [],
        }
        await notify_search(uow.store, job)

    class Telegram(ArticleTelegram):
        async def make_request(self, bot, method, timeout=None):
            if blocked and isinstance(method, SendRichMessage) and method.chat_id == 1:
                raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
            return await super().make_request(bot, method, timeout)

    session = Telegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        bot._me = TelegramUser(id=bot.id, is_bot=True, first_name="Test", username="test_bot")
        assert await deliver_one_outbox(bot, engine) is not blocked
    async with PostgresUnitOfWork(engine) as uow:
        notices = await uow.store.find(Outbox, support_kind="music_note")
        assert len(notices) == 1 and notices[0].chat_id == -100
        text = notices[0].text
        if blocked:
            assert "Откройте личку" in text and "Отправил" not in text
            assert f"musicjob_{identifier.hex}" in notices[0].buttons[0][1]
        else:
            assert "Отправил найденные песни" in text
            assert session.rich_sent[0].chat_id == 1
    recovered = await send(f"/start musicjob_{identifier.hex}", private=True)
    assert recovered.articles
    cells = recovered.articles[0].blocks[1]["cells"]
    assert cells[1][1]["text"]["button"]["text"] == "✅"
    foreign = await send(f"/start musicjob_{identifier.hex}", actor=2, private=True)
    assert not foreign.articles
    # Group buttons can never display the private selection table.
    public = await send(f"mi_view {identifier.hex}")
    assert not public.articles and "Откройте личку" in public.text
