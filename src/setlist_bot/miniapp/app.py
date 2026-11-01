import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qsl
from uuid import UUID

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.domain.entities import Confirmation, User
from setlist_bot.domain.miniapp_auth import verify_init_data
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.runtime import check_schema
from setlist_bot.miniapp.settings import MiniAppSettings
from setlist_bot.transport.i18n import tr

ENGINE = web.AppKey("engine", AsyncEngine)
SETTINGS = web.AppKey("settings", MiniAppSettings)
GATE = web.AppKey("gate", asyncio.Semaphore)
LANGUAGE = web.RequestKey("miniapp-language", str)
logger = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name("static")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors https://web.telegram.org https://*.telegram.org"
    ),
}


@web.middleware
async def errors(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    try:
        response = await handler(request)
    except AppError as exc:
        status = (
            401
            if exc.code == "miniapp_auth"
            else 409
            if exc.code in ("conflict", "selection_catalog_changed")
            else 403
        )
        try:
            message = tr(request.get(LANGUAGE, "ru"), exc.code)
        except KeyError:
            message = tr("ru", "input")
        result = {"error": exc.code, "message": message}
        if exc.code == "admin_suggestions_only" and request.match_info.get("operation") == "launch":
            # RU: Подписанный запуск уже проверен; запрос прав остаётся у существующего бота.
            # EN: Keep permission requests in Telegram, without expanding the public SQL role.
            start = dict(parse_qsl(request.headers["Authorization"][4:])).get("start_param", "")
            target = UUID(start.removeprefix("sl_"))
            result["action_url"] = (
                f"https://t.me/{request.app[SETTINGS].bot_username}?start=draft_{target.hex}"
            )
        response = web.json_response(result, status=status)
    except (ValueError, TypeError, KeyError, IndexError):
        response = web.json_response({"error": "input", "message": tr("ru", "input")}, status=400)
    except web.HTTPException as exc:
        response = web.Response(status=exc.status, text=exc.reason, headers=exc.headers)
    except Exception as exc:
        logger.warning("Mini App request failed (%s)", type(exc).__name__)
        response = web.json_response(
            {"error": "unavailable", "message": "Попробуйте позже / Try again later"}, status=503
        )
    response.headers.update(HEADERS)
    return response


async def asset(request: web.Request) -> web.Response:
    name = request.match_info.get("asset", "index.html")
    types = {
        "index.html": "text/html",
        "app.js": "text/javascript",
        "model.js": "text/javascript",
        "persistence.js": "text/javascript",
        "style.css": "text/css",
    }
    if name not in types:
        raise web.HTTPNotFound()
    return web.Response(
        body=(ASSETS / name).read_bytes(), content_type=types[name], charset="utf-8"
    )


async def api(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS]
    operation = request.match_info["operation"]
    if request.method == "POST" and request.headers.get("Origin") != settings.public_base_url:
        raise AppError("miniapp_auth")
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        raise AppError("miniapp_auth")
    actor = verify_init_data(
        header[4:], base64.urlsafe_b64decode(settings.auth_key.get_secret_value()), allow_group=True
    )
    if request.app[GATE].locked():
        return web.json_response(
            {"error": "busy", "message": "Попробуйте позже / Try again"}, status=503
        )
    async with request.app[GATE]:
        payload = await request.json() if request.method == "POST" else dict(request.query)
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        async with PostgresUnitOfWork(request.app[ENGINE]) as uow:
            users = await uow.store.find(User, telegram_id=actor)
            if not users:
                raise AppError("miniapp_auth")
            user = users[0]
            request[LANGUAGE] = user.language
            service = MiniDrafts(uow.store, user)
            if operation == "launch" and request.method == "GET":
                # RU: Цель берётся только из проверенного Telegram start_param.
                # EN: Never authorize a target supplied through initDataUnsafe or URL alone.
                start = dict(parse_qsl(header[4:])).get("start_param", "")
                if not start.startswith("sl_"):
                    raise AppError("miniapp_auth")
                editor = await service.open_template(UUID(start[3:]))
                result = await service.state(editor.id)
                result["bot_url"] = (
                    f"https://t.me/{settings.bot_username}?start=performance_{result['concert_id']}"
                )
                return web.json_response(result)
            if not isinstance(payload.get("session"), str):
                raise ValueError("Expected a session ID")
            identifier = UUID(payload["session"])
            owner_session = await uow.store.get(Confirmation, identifier)
            if owner_session.user_id != user.id:
                raise AppError("miniapp_auth")
            if request.method == "GET" and operation == "state":
                result = await service.state(identifier)
                result["bot_url"] = (
                    f"https://t.me/{settings.bot_username}?start=performance_{result['concert_id']}"
                )
            elif request.method == "GET" and operation == "history":
                result = await service.history(identifier, int(payload["index"]))
            elif request.method == "POST" and operation == "pause":
                result = {"seconds": await service.pause(identifier, payload["value"])}
            elif request.method == "POST" and operation == "save":
                try:
                    result = await service.save(
                        identifier,
                        payload["version"],
                        payload["entries"],
                        payload["request_id"],
                        default_pause_seconds=payload.get("default_pause_seconds"),
                    )
                except AppError as exc:
                    if exc.code not in ("conflict", "selection_catalog_changed"):
                        raise
                    result = {
                        "error": "conflict",
                        "message": tr(user.language, "conflict"),
                        "latest": await service.state(identifier),
                    }
                    return web.json_response(result, status=409)
                result["bot_url"] = (
                    f"https://t.me/{settings.bot_username}?start=performance_{result['concert_id']}"
                )
            elif request.method == "POST" and operation == "publish":
                from setlist_bot.miniapp.publication import publish

                try:
                    result = await publish(
                        service,
                        identifier,
                        payload["version"],
                        payload["entries"],
                        payload["request_id"],
                    )
                except AppError as exc:
                    if exc.code not in ("conflict", "selection_catalog_changed"):
                        raise
                    return web.json_response(
                        {
                            "error": exc.code,
                            "message": tr(user.language, exc.code),
                            "latest": await service.state(identifier),
                        },
                        status=409,
                    )
            elif request.method == "GET" and operation == "publication":
                result = await service.publication_status(identifier)
            else:
                raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST"])
        return web.json_response(result)


async def health(request: web.Request) -> web.Response:
    await identity(request.app[ENGINE], request.app[SETTINGS])
    return web.json_response({"status": "ok"})


async def identity(engine: AsyncEngine, settings: MiniAppSettings) -> None:
    await check_schema(engine)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT bot_id,environment FROM deployment_identity WHERE key='main'")
            )
        ).one_or_none()
    if row is None or tuple(row) != (settings.expected_bot_id, settings.environment):
        raise RuntimeError("Mini App deployment identity mismatch")


def create_app(settings: MiniAppSettings, engine: AsyncEngine | None = None) -> web.Application:
    owned = engine is None
    engine = engine or create_async_engine(
        settings.database_url,
        pool_size=3,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"timeout": 5, "command_timeout": 10},
    )
    app = web.Application(middlewares=[errors], client_max_size=100 * 1024)
    app[ENGINE], app[SETTINGS], app[GATE] = engine, settings, asyncio.Semaphore(4)
    app.router.add_get("/miniapp/", asset)
    app.router.add_get("/miniapp/{asset}", asset)
    app.router.add_route("GET", "/miniapp/api/{operation}", api)
    app.router.add_route("POST", "/miniapp/api/{operation}", api)
    app.router.add_get("/healthz", health)

    async def startup(app: web.Application) -> None:
        await identity(app[ENGINE], settings)

    async def cleanup(app: web.Application) -> None:
        if owned:
            await app[ENGINE].dispose()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
