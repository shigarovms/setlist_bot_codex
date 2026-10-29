from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message, Update
from aiogram.types import User as TelegramUser
from test_collective_workflows import action, client
from test_telegram import FakeTelegram

from setlist_bot.domain.entities import ChatBinding, Confirmation, Group, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.main import deliver_one_outbox
from setlist_bot.transport.telegram import Processor, make_router, send_response

TOKEN = "123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN"


async def test_automatic_greeting_is_only_for_join_and_distinguishes_registered_chat(engine, world):
    session = FakeTelegram()
    dispatcher = Dispatcher()
    dispatcher.include_router(make_router(Processor(engine), "test_bot"))
    bot_user = TelegramUser(id=123456, is_bot=True, first_name="Bot")
    async with Bot(TOKEN, session=session) as bot:
        for number, chat in enumerate((-300, -100), 91000):
            update = Update(
                update_id=number,
                my_chat_member=ChatMemberUpdated(
                    chat=Chat(id=chat, type="supergroup"),
                    from_user=TelegramUser(id=1, is_bot=False, first_name="Owner"),
                    date=datetime.now(UTC),
                    old_chat_member=ChatMemberLeft(user=bot_user),
                    new_chat_member=ChatMemberMember(user=bot_user),
                ),
            )
            await dispatcher.feed_update(bot, update)
            await dispatcher.feed_update(bot, update)
        assert len(session.sent) == 2
        assert "/group" in session.sent[0].text and "уже зарегистрирован" in session.sent[1].text
        unchanged = update.model_copy(
            update={
                "update_id": 91002,
                "my_chat_member": update.my_chat_member.model_copy(
                    update={"old_chat_member": ChatMemberMember(user=bot_user)}
                ),
            }
        )
        await dispatcher.feed_update(bot, unchanged)
        assert len(session.sent) == 2
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=91003,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=-100, type="group"),
                    migrate_to_chat_id=-100999,
                    from_user=bot_user,
                ),
            ),
        )
        async with PostgresUnitOfWork(engine) as uow:
            assert (await uow.store.find(ChatBinding, group_id=world.group.id))[
                0
            ].chat_id == -100999


async def test_owner_unreachable_is_reported_and_notice_deleted_after_request(engine, world):
    send = client(engine)
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(Group, world.group.id)).admins_only_suggestions = True
    await send(f"/use_concert {world.concert.id}", actor=2)
    notice = await send("/suggestion", actor=2, remember=300)
    response = await send(action(notice, "cr_request"), actor=2)

    class Blocked(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, SendMessage) and method.chat_id == 1:
                raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
            return await super().make_request(bot, method, timeout)

    session = Blocked()
    async with Bot(TOKEN, session=session) as bot:
        await send_response(bot, -100, response)
        assert [m.message_id for m in session.deleted] == [300]
        assert not await deliver_one_outbox(bot, engine)
    async with PostgresUnitOfWork(engine) as uow:
        errors = await uow.store.find(Outbox, support_kind="role_done")
        assert len(errors) == 1 and errors[0].chat_id == -100
        assert "Не удалось доставить" in errors[0].text
        requests = [
            r
            for r in await uow.store.find(Confirmation, user_id=world.member.id)
            if '"kind": "role_request"' in r.command
        ]
        assert requests and '"undeliverable"' in requests[0].command
