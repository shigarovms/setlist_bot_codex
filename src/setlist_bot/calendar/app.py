import asyncio
import hashlib
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.application.calendars import token_hash
from setlist_bot.calendar.settings import CalendarSettings
from setlist_bot.domain.calendar import render_calendar
from setlist_bot.domain.entities import now
from setlist_bot.infrastructure.calendar_queries import CalendarQueries
from setlist_bot.infrastructure.runtime import check_schema

logger = logging.getLogger(__name__)
ENGINE = web.AppKey("engine", AsyncEngine)
SETTINGS = web.AppKey("settings", CalendarSettings)
GATE = web.AppKey("gate", asyncio.Semaphore)
CACHE = web.AppKey("cache", OrderedDict[str, bytes])
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_FEED_BYTES = 4 * 1024 * 1024


@web.middleware
async def safe_errors(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        # RU: Ни URI, ни сообщение исключения не пишем: они могут содержать ссылку.
        # EN: Public logs contain only exception types, never request paths or SQL parameters.
        logger.warning("Calendar request failed (%s)", type(exc).__name__)
        return web.Response(
            status=503, text="Temporarily unavailable", headers={"Cache-Control": "no-store"}
        )


async def health(request: web.Request) -> web.Response:
    await check_identity(request.app[ENGINE], request.app[SETTINGS])
    return web.json_response({"status": "ok"})


async def feed(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return web.Response(status=404, headers={"Cache-Control": "no-store"})
    if request.app[GATE].locked():
        # RU/EN: Bound admission too, not just SQL concurrency; do not queue unlimited readers.
        return web.Response(status=503, headers={"Retry-After": "60", "Cache-Control": "no-store"})
    async with request.app[GATE]:
        # RU: Авторизация и актуальный состав проверяются ДО обращения к кешу/ETag.
        # EN: Revoked memberships must never receive an old cached calendar or 304.
        result = await CalendarQueries(request.app[ENGINE]).read(token_hash(token))
        if result is None:
            return web.Response(status=404, headers={"Cache-Control": "no-store"})
        subscription, events = result
        settings = request.app[SETTINGS]
        signature = hashlib.sha256(
            repr(
                (
                    events,
                    subscription["language"],
                    1,  # RU/EN: Calendar format version is part of semantic cache identity.
                    settings.expected_bot_id,
                    settings.bot_username,
                )
            ).encode()
        ).hexdigest()
        cache = request.app[CACHE]
        body = cache.get(signature)
        if body is None:
            body = render_calendar(
                events,
                language=str(subscription["language"]),
                generated_at=now(),
                bot_id=settings.expected_bot_id,
                username=settings.bot_username,
            )
            if len(body) > MAX_FEED_BYTES:
                raise OverflowError("Calendar response too large")
            cache[signature] = body
            while len(cache) > 128 or sum(len(value) for value in cache.values()) > MAX_CACHE_BYTES:
                cache.popitem(last=False)
        else:
            cache.move_to_end(signature)
        # RU: Время сборки DTSTAMP не меняет смысл событий. EN: A weak validator
        # permits 304 across process restarts while still invalidating every real change.
        etag = '"' + signature + '"'
        headers = {
            "ETag": "W/" + etag,
            "Cache-Control": "private, no-cache, must-revalidate",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Disposition": 'inline; filename="performances.ics"',
        }
        validators = request.headers.get("If-None-Match", "").split(",")
        if any(value.strip().removeprefix("W/") in (etag, "*") for value in validators):
            return web.Response(status=304, headers=headers)
        return web.Response(
            body=body, content_type="text/calendar", charset="utf-8", headers=headers
        )


async def check_identity(engine: AsyncEngine, settings: CalendarSettings) -> None:
    await check_schema(engine)
    async with engine.connect() as connection:
        identity = (
            await connection.execute(
                text("SELECT bot_id,environment FROM deployment_identity WHERE key='main'")
            )
        ).one_or_none()
    if identity is None or tuple(identity) != (settings.expected_bot_id, settings.environment):
        raise RuntimeError("Calendar deployment identity mismatch")


def create_app(settings: CalendarSettings, engine: AsyncEngine | None = None) -> web.Application:
    owned = engine is None
    engine = engine or create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={
            "timeout": 5,
            "command_timeout": 5,
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "4000",
            },
        },
    )
    app = web.Application(middlewares=[safe_errors], client_max_size=1024)
    app[ENGINE], app[SETTINGS] = engine, settings
    app[GATE], app[CACHE] = asyncio.Semaphore(4), OrderedDict()
    app.router.add_get("/calendar/{token}.ics", feed)
    app.router.add_get("/healthz", health)

    async def startup(app: web.Application) -> None:
        await check_identity(app[ENGINE], settings)

    async def cleanup(app: web.Application) -> None:
        if owned:
            await app[ENGINE].dispose()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
