import argparse
import asyncio
import contextlib
import logging
from uuid import UUID

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.application.setlist_menus import REQUEST, SetlistMenus
from setlist_bot.application.trash import TrashCleanup
from setlist_bot.config import Settings
from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    Group,
    Membership,
    Outbox,
    User,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork, metadata
from setlist_bot.transport.commands import collect_command_targets, synchronize_commands
from setlist_bot.transport.controller import Response
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.session import VerifiedSession
from setlist_bot.transport.telegram import Processor, make_router, send_response

logger = logging.getLogger(__name__)


async def deliver_one_outbox(bot: Bot, engine: AsyncEngine) -> None:
    async with PostgresUnitOfWork(engine) as uow:
        pending = (
            await uow.session.scalars(
                select(Outbox).where(metadata.tables["outbox"].c.sent.is_(False)).limit(1)
            )
        ).first()
        if pending is None:
            return
        group = (
            await uow.store.get(Group, pending.group_id, lock=True) if pending.group_id else None
        )
        event = await uow.store.get(Outbox, pending.id, lock=True)
        if event.sent:
            return
        request = None
        request_data = None
        if group is not None:
            bindings = await uow.store.find(ChatBinding, group_id=group.id, chat_id=event.chat_id)
            concert = await uow.store.get(Concert, event.concert_id) if event.concert_id else None
            if event.chat_id > 0 and event.buttons and event.buttons[0][1].startswith("slreq "):
                requests = await uow.store.find(
                    Confirmation, id=UUID(event.buttons[0][1].split()[1])
                )
                if requests:
                    request = requests[0]
                    request_data = SetlistMenus.data(request)
                    admin = await uow.store.get(User, request.user_id)
                    if not (
                        request_data.get("kind") == REQUEST
                        and request_data["status"] == "pending"
                        and request.expires_at > now()
                        and request.chat_id == event.chat_id == admin.telegram_id
                        and request_data["group_id"] == str(event.group_id)
                        and request_data["concert_id"] == str(event.concert_id)
                        and await uow.store.find(
                            Membership,
                            group_id=group.id,
                            user_id=admin.id,
                            role="admin",
                            status="active",
                        )
                        and await uow.store.find(
                            Membership,
                            group_id=group.id,
                            user_id=UUID(request_data["requester_id"]),
                            status="active",
                        )
                    ):
                        if (
                            request_data.get("kind") == REQUEST
                            and request_data.get("status") == "pending"
                        ):
                            SetlistMenus.write(request, {**request_data, "status": "stale"})
                        event.sent = True
                        return
                    try:
                        await SetlistMenus(uow.store, admin, event.chat_id).validate(
                            request_data, fresh=True
                        )
                    except AppError:
                        SetlistMenus.write(request, {**request_data, "status": "stale"})
                        event.sent = True
                        return
            if (
                group.deleted_at is not None
                or (not bindings and request is None)
                or (concert and concert.deleted_at is not None)
            ):
                event.sent = True
                return
        try:
            await send_response(
                bot,
                event.chat_id,
                Response(
                    event.text, [(b[0], b[1]) for b in event.buttons], monospace=event.monospace
                ),
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if request is None or request_data is None or group is None:
                raise
            if isinstance(exc, TelegramBadRequest) and not any(
                reason in exc.message.lower() for reason in ("chat not found", "user not found")
            ):
                raise
            SetlistMenus.write(request, {**request_data, "status": "undeliverable"})
            uow.store.add(
                Outbox(
                    group_id=group.id,
                    concert_id=event.concert_id,
                    chat_id=request_data["source_chat_id"],
                    text=tr(group.language, "request_delivery_failed"),
                )
            )
        event.sent = True


async def deliver_outbox(bot: Bot, engine: AsyncEngine) -> None:
    while True:
        try:
            await deliver_one_outbox(bot, engine)
        except Exception as exc:
            logger.warning("Outbox delivery failed (%s); will retry", type(exc).__name__)
        await asyncio.sleep(2)


async def cleanup_trash(engine: AsyncEngine) -> None:
    while True:
        try:
            async with PostgresUnitOfWork(engine) as uow:
                await TrashCleanup(uow.store).run()
        except Exception as exc:
            logger.warning("Trash cleanup failed (%s); will retry", type(exc).__name__)
        await asyncio.sleep(60)


async def run(*, check: bool = False, sync_only: bool = False) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            version: str = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            await connection.execute(text("SELECT id FROM groups LIMIT 1"))
        if check:
            print(f"PostgreSQL OK; migration {version}")
            return
        if settings.bot_token is None or not settings.bot_token.get_secret_value():
            raise SystemExit("Set BOT_TOKEN in .env; see README.md. Never put tokens in Git.")
        async with Bot(settings.bot_token.get_secret_value(), session=VerifiedSession()) as bot:
            me = await bot.get_me()
            try:
                targets = await collect_command_targets(
                    engine,
                    extra_scopes=settings.bot_command_extra_scopes,
                    legacy_languages=settings.bot_command_legacy_languages,
                )
                report = await synchronize_commands(bot, targets)
            except Exception as exc:
                logger.error("Command menu synchronization failed (%s)", type(exc).__name__)
                raise SystemExit(
                    "Command menu synchronization failed; retry startup or --sync-commands"
                ) from None
            if sync_only:
                print(
                    f"Telegram commands OK; checked {report.checked}; removed {report.removed}; "
                    f"registered {report.registered}; unavailable {report.unavailable}"
                )
                return
            dispatcher = Dispatcher()
            dispatcher.include_router(make_router(Processor(engine), me.username or ""))
            workers = [
                asyncio.create_task(deliver_outbox(bot, engine)),
                asyncio.create_task(cleanup_trash(engine)),
            ]
            try:
                await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])
            finally:
                for worker in workers:
                    worker.cancel()
                for worker in workers:
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check", action="store_true", help="Check PostgreSQL without contacting Telegram"
    )
    action.add_argument(
        "--sync-commands", action="store_true", help="Synchronize Telegram menus without polling"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(check=args.check, sync_only=args.sync_commands))
