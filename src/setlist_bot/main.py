import argparse
import asyncio
import contextlib
import json
import logging
from datetime import datetime, time, timedelta
from uuid import UUID

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.application import reminders
from setlist_bot.application.chat_cleanup import message_ids as known_message_ids
from setlist_bot.application.setlist_menus import REQUEST, SetlistMenus
from setlist_bot.application.trash import TrashCleanup
from setlist_bot.config import Settings
from setlist_bot.domain.entities import (
    ChatBinding,
    Concert,
    Confirmation,
    Group,
    Membership,
    MusicSearch,
    Outbox,
    Reminder,
    RuntimeStatus,
    Setlist,
    Submission,
    SupportMessage,
    SupportSettings,
    User,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork, metadata
from setlist_bot.infrastructure.music_worker import search_worker
from setlist_bot.infrastructure.runtime import BotInstance, check_schema, identity_details
from setlist_bot.infrastructure.telemetry import Monitor, PollingStatusHandler
from setlist_bot.transport.commands import collect_command_targets, synchronize_commands
from setlist_bot.transport.controller import Controller, Response
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.session import VerifiedSession
from setlist_bot.transport.telegram import (
    Processor,
    delete_selection_message,
    make_router,
    remember_setlist_response,
    send_response,
)

logger = logging.getLogger(__name__)


async def deliver_card_refresh(bot: Bot, uow: PostgresUnitOfWork, event: Outbox) -> None:
    """RU: Обновляем только связанную общую карточку; старые задания безопасно завершаются.

    EN: Reuse the normal renderer and message binding; do not publish private content.
    """
    cards = await uow.store.find(
        Confirmation, id=UUID(event.payload["card_id"]), chat_id=event.chat_id
    )
    group = await uow.store.get(Group, event.group_id) if event.group_id else None
    if not cards or group is None or group.deleted_at is not None or group.owner_id is None:
        event.sent = True
        return
    card = cards[0]
    data = SetlistMenus.data(card)
    setlists = await uow.store.find(Setlist, group_id=group.id, concert_id=event.concert_id)
    bindings = await uow.store.find(ChatBinding, group_id=group.id, chat_id=event.chat_id)
    if (
        not bindings
        or not setlists
        or card.expires_at <= now()
        or data.get("kind") != "setlist_menu"
        or data.get("closed")
        or data.get("personal")
        or data.get("private_chat")
        or data.get("owner_id") is not None
        or not data.get("message_ids")
        or data.get("group_id") != str(group.id)
        or data.get("concert_id") != str(event.concert_id)
        or data.get("version") != setlists[0].version
        or data.get("approved") != setlists[0].approved
    ):
        event.sent = True
        return
    owner = await uow.store.get(User, group.owner_id)
    runtime_rows = await uow.store.find(RuntimeStatus, component="bot")
    username = runtime_rows[0].details.get("bot_username", "") if runtime_rows else ""
    controller = Controller(uow.store, owner, event.chat_id, private=False, bot_username=username)
    controller.language = group.language
    try:
        await controller.menus.validate(data)
    except AppError:
        event.sent = True
        return
    response = await controller.refresh_shared_card(card)
    messages = await send_response(bot, event.chat_id, response)
    await remember_setlist_response(uow.store, event.chat_id, response, messages)
    event.sent, event.delivered_at = True, now()
    event.attempts += 1
    event.last_error, event.next_attempt_at = "", None


async def deliver_one_outbox(bot: Bot, engine: AsyncEngine) -> bool:
    event_id = None
    try:
        async with PostgresUnitOfWork(engine) as uow:
            pending = (
                await uow.session.scalars(
                    select(Outbox)
                    .where(
                        metadata.tables["outbox"].c.sent.is_(False),
                        or_(
                            metadata.tables["outbox"].c.next_attempt_at.is_(None),
                            metadata.tables["outbox"].c.next_attempt_at <= now(),
                        ),
                    )
                    .order_by(
                        metadata.tables["outbox"].c.created_at.asc().nulls_first(),
                        metadata.tables["outbox"].c.id,
                    )
                    .limit(1)
                )
            ).first()
            if pending is None:
                return False
            event_id = pending.id
            group = (
                await uow.store.get(Group, pending.group_id, lock=True)
                if pending.group_id
                else None
            )
            event = await uow.store.get(Outbox, pending.id, lock=True)
            if event.sent:
                return False
            if event.support_kind == "music":
                from setlist_bot.infrastructure.workflow_delivery import deliver_music

                return await deliver_music(bot, uow, event)
            if event.support_kind == "collective":
                from setlist_bot.infrastructure.workflow_delivery import deliver_collective

                return await deliver_collective(bot, uow, event)
            if event.support_kind == "refresh":
                try:
                    await deliver_card_refresh(bot, uow, event)
                except TelegramForbiddenError as exc:
                    event.sent, event.discarded_at = True, now()
                    event.attempts += 1
                    event.last_error = type(exc).__name__
                return event.delivered_at is not None
            if event.support_kind in ("event", "request"):
                settings = await uow.store.find(SupportSettings, key="main")
                if not settings:
                    event.sent = True
                    return False
                # RU: После смены чата даже уже поставленные в очередь обращения идут в новый чат.
                # EN: Reconfiguration also redirects pending support events to the new destination.
                event.chat_id = settings[0].chat_id
                for route in await uow.store.find(SupportMessage, outbox_id=event.id):
                    route.chat_id = event.chat_id
            reminders_for_event = await uow.store.find(Reminder, outbox_id=event.id)
            if reminders_for_event:
                setlists = await uow.store.find(Setlist, concert_id=event.concert_id)
                if (
                    group is None
                    or not group.reminders_enabled
                    or not setlists
                    or setlists[0].approved
                    or reminders_for_event[0].day != now().astimezone(reminders.MOSCOW).date()
                ):
                    event.sent = True
                    return False
            request = None
            request_data = None
            if group is not None:
                bindings = await uow.store.find(
                    ChatBinding, group_id=group.id, chat_id=event.chat_id
                )
                concert = (
                    await uow.store.get(Concert, event.concert_id) if event.concert_id else None
                )
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
                            return False
                        try:
                            await SetlistMenus(uow.store, admin, event.chat_id).validate(
                                request_data, fresh=True
                            )
                        except AppError:
                            SetlistMenus.write(request, {**request_data, "status": "stale"})
                            event.sent = True
                            return False
                if (
                    group.deleted_at is not None
                    or (not bindings and request is None)
                    or (concert and concert.deleted_at is not None)
                ):
                    event.sent = True
                    return False
            try:
                event.attempts += 1
                response = Response(
                    event.text,
                    [(b[0], b[1]) for b in event.buttons],
                    monospace=event.monospace,
                    monospace_body=event.text[event.monospace_offset :]
                    if event.monospace
                    else None,
                )
                if group is not None and event.concert_id and event.payload.get("setlist_snapshot"):
                    # This payload exists only after explicit publication, never for private drafts.
                    concert = await uow.store.get(Concert, event.concert_id)
                    actor = await uow.store.get(User, group.owner_id) if group.owner_id else None
                    if actor:
                        controller = Controller(uow.store, actor, event.chat_id, private=False)
                        controller.language = group.language
                        response = await controller._style_setlist(
                            response,
                            event.payload["setlist_snapshot"],
                            concert,
                            event.payload["setlist_version"],
                            private=True,
                            prefix=event.text[: event.monospace_offset],
                        )
                sent_messages = await send_response(bot, event.chat_id, response)
                event.delivered_at = now()
                event.last_error = ""
                event.next_attempt_at = None
                if request is not None and request_data is not None:
                    SetlistMenus.write(
                        request,
                        {**request_data, "message_ids": [m.message_id for m in sent_messages]},
                    )
                for button in event.buttons:
                    if button[1].startswith("apply_submission ") and sent_messages:
                        submission_id = UUID(button[1].split()[1])
                        submission = await uow.store.get(Submission, submission_id)
                        uow.store.add(
                            Confirmation(
                                user_id=submission.author_id,
                                chat_id=event.chat_id,
                                command=json.dumps(
                                    {
                                        "kind": "submission_panel",
                                        "group_id": str(event.group_id),
                                        "concert_id": str(event.concert_id),
                                        "submission_id": str(submission_id),
                                        "message_ids": [m.message_id for m in sent_messages],
                                    }
                                ),
                                expires_at=now() + timedelta(days=30),
                            )
                        )
                routes = await uow.store.find(SupportMessage, outbox_id=event.id)
                if routes and sent_messages:
                    routes[0].message_id = sent_messages[0].message_id
                    for message in sent_messages[1:]:
                        uow.store.add(
                            SupportMessage(
                                outbox_id=event.id,
                                user_id=routes[0].user_id,
                                chat_id=event.chat_id,
                                message_id=message.message_id,
                            )
                        )
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                event.last_error = type(exc).__name__
                if request is None or request_data is None or group is None:
                    if event.support_kind and (
                        isinstance(exc, TelegramForbiddenError)
                        or any(
                            reason in exc.message.lower()
                            for reason in ("chat not found", "user not found", "bot was kicked")
                        )
                    ):
                        logger.warning(
                            "Notification destination unavailable; retiring event %s", event.id
                        )
                        event.sent = True
                        if event.support_kind == "reply":
                            settings = await uow.store.find(SupportSettings, key="main")
                            if settings:
                                uow.store.add(
                                    Outbox(
                                        chat_id=settings[0].chat_id,
                                        support_kind="event",
                                        text=(
                                            "Ответ поддержки не доставлен пользователю "
                                            f"{event.chat_id}. "
                                            "Бот заблокирован или личный чат недоступен."
                                        ),
                                    )
                                )
                        return False
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
            return event.delivered_at is not None
    except Exception as exc:
        if event_id is not None:
            try:
                async with PostgresUnitOfWork(engine) as failed:
                    event = await failed.store.get(Outbox, event_id, lock=True)
                    if not event.sent:
                        event.attempts += 1
                        event.last_error = type(exc).__name__[:120]
                        retry_after = getattr(exc, "retry_after", 0)
                        delay = max(2, min(3600, max(retry_after, 2 ** min(event.attempts, 10))))
                        event.next_attempt_at = now() + timedelta(seconds=delay)
            except Exception as metadata_error:
                logger.warning(
                    "Could not persist delivery failure (%s)", type(metadata_error).__name__
                )
        raise


async def deliver_outbox(bot: Bot, engine: AsyncEngine, monitor: Monitor | None = None) -> None:
    while True:
        try:
            delivered = await deliver_one_outbox(bot, engine)
            if monitor:
                monitor.set("outbox", "running", success=delivered)
        except Exception as exc:
            if monitor:
                monitor.set("outbox", "retrying", error=type(exc).__name__)
            logger.warning("Outbox delivery failed (%s); will retry", type(exc).__name__)
        await asyncio.sleep(2)


async def cleanup_trash(engine: AsyncEngine, bot: Bot, monitor: Monitor | None = None) -> None:
    while True:
        try:
            async with PostgresUnitOfWork(engine) as uow:
                for expired in await uow.store.expired(Confirmation, "expires_at", now()):
                    data = json.loads(expired.command)
                    if data.get("kind") not in ("setlist_menu", "submission_panel"):
                        for identifier in known_message_ids(data):
                            await delete_selection_message(bot, expired.chat_id, identifier)
                for expired_music in await uow.store.expired(MusicSearch, "expires_at", now()):
                    for identifier in expired_music.data.get("panel_ids", []):
                        await delete_selection_message(bot, expired_music.chat_id, identifier)
                await TrashCleanup(uow.store).run()
            if monitor:
                monitor.set("cleanup", "waiting", next_run=now() + timedelta(seconds=60))
        except Exception as exc:
            if monitor:
                monitor.set("cleanup", "retrying", error=type(exc).__name__)
            logger.warning("Trash cleanup failed (%s); will retry", type(exc).__name__)
        await asyncio.sleep(60)


async def daily_reminders(engine: AsyncEngine, monitor: Monitor | None = None) -> None:
    # RU: Один план в день, затем ожидание назначенного времени каждого коллектива.
    # EN: Build one daily plan, sleep until each due time, and deduplicate in PostgreSQL.
    while True:
        try:
            local = now().astimezone(reminders.MOSCOW)
            start = datetime.combine(local.date(), time(14), reminders.MOSCOW)
            if local < start:
                if monitor:
                    monitor.set("reminders", "waiting", next_run=start)
                await asyncio.sleep((start - local).total_seconds())
            async with PostgresUnitOfWork(engine) as uow:
                plan = await reminders.plan(uow.store, now())
            for due, concert_id in plan:
                delay = (due - now()).total_seconds()
                if delay > 0:
                    if monitor:
                        monitor.set("reminders", "waiting", next_run=due)
                    await asyncio.sleep(delay)
                async with PostgresUnitOfWork(engine) as uow:
                    await reminders.queue(uow.store, concert_id, now())
            tomorrow = now().astimezone(reminders.MOSCOW).date() + timedelta(days=1)
            next_run = datetime.combine(tomorrow, time(14), reminders.MOSCOW)
            if monitor:
                monitor.set("reminders", "waiting", next_run=next_run)
            await asyncio.sleep(max(1, (next_run - now()).total_seconds()))
        except Exception as exc:
            if monitor:
                monitor.set("reminders", "retrying", error=type(exc).__name__)
            logger.warning("Reminder planning failed (%s); retrying", type(exc).__name__)
            await asyncio.sleep(60)


async def run(*, check: bool = False, sync_only: bool = False) -> None:
    settings = Settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": 8, "command_timeout": 15},
    )
    try:
        version = await check_schema(engine)
        if check:
            print(f"PostgreSQL OK; migration {version}")
            return
        if settings.bot_token is None or not settings.bot_token.get_secret_value():
            raise SystemExit("Set BOT_TOKEN in .env; see README.md. Never put tokens in Git.")
        monitor = Monitor()
        async with Bot(
            settings.bot_token.get_secret_value(), session=VerifiedSession(monitor)
        ) as bot:
            me = await bot.get_me()
            if settings.expected_bot_id is not None and me.id != settings.expected_bot_id:
                raise SystemExit(
                    "Bot identity does not match EXPECTED_BOT_ID; polling is forbidden"
                )
            async with BotInstance(engine, me.id, settings.runtime_environment) as instance:
                monitor.identity = identity_details(
                    me.id,
                    me.username or "",
                    settings.runtime_environment,
                    settings.runtime_location,
                )
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
                        f"Telegram commands OK; checked {report.checked}; "
                        f"removed {report.removed}; "
                        f"registered {report.registered}; unavailable {report.unavailable}"
                    )
                    return
                monitor.set("bot", "polling")
                monitor.set("telegram", "no_errors_recorded")
                from setlist_bot.infrastructure.ux_analytics import UXBuffer

                analytics = UXBuffer()
                dispatcher = Dispatcher()
                dispatcher.include_router(
                    make_router(
                        Processor(
                            engine,
                            bot_username=me.username or "",
                            monitor=monitor,
                            analytics=analytics,
                            calendar_public_base_url=settings.calendar_public_base_url,
                        ),
                        me.username or "",
                        diagnostics=settings.telegram_diagnostics,
                    )
                )
                workers = [
                    asyncio.create_task(deliver_outbox(bot, engine, monitor)),
                    asyncio.create_task(cleanup_trash(engine, bot, monitor)),
                    asyncio.create_task(daily_reminders(engine, monitor)),
                    asyncio.create_task(monitor.persist(engine)),
                    asyncio.create_task(search_worker(engine, monitor)),
                    asyncio.create_task(analytics.run(engine, monitor)),
                ]
                polling = asyncio.create_task(
                    dispatcher.start_polling(
                        bot, allowed_updates=["message", "callback_query", "my_chat_member"]
                    )
                )
                watchdog = asyncio.create_task(instance.watch())
                polling_status = PollingStatusHandler(monitor)
                polling_logger = logging.getLogger("aiogram.dispatcher")
                polling_logger.addHandler(polling_status)
                try:
                    done, _ = await asyncio.wait(
                        [polling, watchdog], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        task.result()
                except Exception as exc:
                    monitor.set("bot", "stopped", error=type(exc).__name__)
                    raise
                finally:
                    polling_logger.removeHandler(polling_status)
                    for task in [polling, watchdog, *workers]:
                        task.cancel()
                    await asyncio.gather(polling, watchdog, *workers, return_exceptions=True)
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(analytics.flush(engine), timeout=3)
                    monitor.set("bot", "stopped")
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(monitor.flush(engine), timeout=3)
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
