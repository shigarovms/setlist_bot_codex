"""Authenticated HTML administration. This service never starts Telegram polling."""

import asyncio
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.admin import views as v
from setlist_bot.admin.security import (
    COOKIE,
    LOGIN_COOKIE,
    SESSION_LIFETIME,
    AdminSettings,
    digest,
    form_proof,
    login_token,
    password_matches,
    sign,
    valid_login_token,
)
from setlist_bot.application.administration import Administration
from setlist_bot.domain.display import entry_labels
from setlist_bot.domain.entities import AdminAudit, AdminLoginLimit, AdminSession, now
from setlist_bot.domain.rules import AppError, clock, flat_content, totals
from setlist_bot.infrastructure.admin_queries import PAGE_SIZE, AdminQueries
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.runtime import (
    SCHEMA_REVISION,
    SchemaMismatch,
    build_revision,
    check_schema,
)
from setlist_bot.transport.i18n import tr

logger = logging.getLogger(__name__)
HANDLER = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass
class State:
    settings: AdminSettings
    engine: AsyncEngine
    queries: AdminQueries
    external_engine: bool = False
    cache: dict[str, Any] = field(default_factory=dict)
    cache_until: float = 0
    cache_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    password_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    schema_checked_until: float = 0
    revision: str = field(default_factory=build_revision)


STATE = web.AppKey("admin_state", State)
SESSION = web.RequestKey("admin_session", AdminSession)


def auth(request: web.Request) -> AdminSession:
    return request[SESSION]


def cookie_name(request: web.Request, base: str = COOKIE) -> str:
    # RU: Старые cookies с Path=/ не должны перекрывать cookies нового контура.
    # EN: Distinct names also isolate a console upgrade from legacy root-path cookies.
    settings = request.app[STATE].settings
    return base + "_" + settings.contour if settings.url_prefix else base


def render(request: web.Request, title: str, body: str, section: str = "") -> web.Response:
    state = request.app[STATE]
    notices = {
        "saved": "Изменения сохранены. Действие записано в журнал.",
        "queued": "Ответ поставлен в очередь. Статус доставки появится в обращении.",
        "duplicate": "Эта форма уже была обработана. Повторное действие не выполнено.",
    }
    return web.Response(
        text=v.page(
            state.settings,
            request.cookies.get(cookie_name(request), ""),
            title,
            body,
            section=section,
            notice=notices.get(request.query.get("notice", ""), ""),
        ),
        content_type="text/html",
    )


def action_form(
    request: web.Request, action: str, target: Any, body: str, label: str, danger: bool = False
) -> str:
    if request.app[STATE].settings.read_only:
        return '<p class="hint">Контур открыт только для просмотра.</p>'
    return v.form(
        request.app[STATE].settings,
        request.cookies[cookie_name(request)],
        action,
        target,
        body,
        label,
        danger=danger,
    )


@web.middleware
async def boundary(request: web.Request, handler: HANDLER) -> web.StreamResponse:
    try:
        settings = request.app[STATE].settings
        host = urlsplit("//" + request.host).hostname or ""
        if host.lower() not in {x.strip().lower() for x in settings.allowed_hosts.split(",")}:
            raise web.HTTPForbidden(text="Недопустимый адрес админки")
        state = request.app[STATE]
        local_path = request.path.removeprefix(settings.url_prefix)
        if not local_path.startswith("/static/") and time.monotonic() > state.schema_checked_until:
            await check_schema(state.engine)
            if settings.console_switch:
                identity = await state.queries.one(
                    "SELECT bot_id,environment FROM deployment_identity WHERE key='main'"
                )
                expected = 7430933141 if settings.contour == "production" else 1110437563
                if identity and (
                    identity["bot_id"] != expected or identity["environment"] != settings.contour
                ):
                    raise web.HTTPConflict(
                        text="Подключённая база привязана к другому боту или контуру. Проверьте настройки подключения."
                    )
            state.schema_checked_until = time.monotonic() + 20
        response = await handler(request)
    except SchemaMismatch as exc:
        response = web.Response(status=503, text=str(exc))
    except web.HTTPException as exc:
        response = exc
    except (ValueError, KeyError):
        response = web.Response(status=400, text="Проверьте адрес и поля формы.")
    except AppError as exc:
        message = (
            tr("ru", exc.code)
            if exc.code
            in (
                "not_found",
                "forbidden",
                "already_done",
                "confirmation",
                "conflict",
                "in_trash",
                "input",
            )
            else "Действие недоступно."
        )
        if exc.code == "conflict":
            message = "Данные изменились после открытия формы. Обновите страницу и проверьте последние действия."
        response = web.Response(status=409, text=message)
    except Exception as exc:
        logger.error("Admin request failed (%s)", type(exc).__name__)
        response = web.Response(
            status=503,
            text="Сервис временно недоступен. Повторите позже; проверьте состояние базы.",
        )
    if isinstance(response, web.HTTPException):
        regular = web.Response(status=response.status, headers=response.headers, body=response.body)
        regular.cookies.update(response.cookies)
        response = regular
    response.headers.update(
        {
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            # RU: no-referrer делает Origin POST-форм равным null и ломает вход.
            # EN: Preserve same-origin form origins; send no referrer to other sites.
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
    )
    prefix = request.app[STATE].settings.url_prefix
    if prefix:
        location = response.headers.get("Location", "")
        if location.startswith("/") and not location.startswith(prefix + "/"):
            response.headers["Location"] = prefix + location
        if (
            response.content_type == "text/html"
            and isinstance(response, web.Response)
            and response.text
        ):
            response.text = re.sub(
                r'(href|action|src)="/(?!/|_contour/)',
                lambda match: match[1] + '="' + prefix + "/",
                response.text,
            )
        for cookie in response.cookies.values():
            cookie["path"] = prefix
    return response


@web.middleware
async def authenticate(request: web.Request, handler: HANDLER) -> web.StreamResponse:
    local_path = request.path.removeprefix(request.app[STATE].settings.url_prefix)
    if local_path in ("/login", "/healthz") or local_path.startswith("/static/"):
        return await handler(request)
    state = request.app[STATE]
    token = request.cookies.get(cookie_name(request), "")
    if not re.fullmatch("[a-f0-9]{64}", token):
        raise web.HTTPSeeOther("/login")
    async with PostgresUnitOfWork(state.engine) as uow:
        sessions = await uow.store.find(
            AdminSession, token_hash=sign(state.settings, "session:" + token)
        )
        if (
            not sessions
            or sessions[0].expires_at <= now()
            or sessions[0].actor != state.settings.username
        ):
            raise web.HTTPSeeOther("/login")
        request[SESSION] = sessions[0]
    if request.method not in ("GET", "HEAD", "POST"):
        raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST"])
    return await handler(request)


def check_origin(request: web.Request) -> None:
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlsplit(origin)
        # Local SSH tunnel has an HTTP origin; reverse-proxy HTTPS is supported too.
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.host:
            raise web.HTTPForbidden(text="Недопустимый источник формы")
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise web.HTTPForbidden(text="Межсайтовая отправка запрещена")


async def login_get(request: web.Request) -> web.Response:
    settings = request.app[STATE].settings
    token = login_token(settings)
    response = web.Response(text=v.login(settings.username, token), content_type="text/html")
    response.set_cookie(
        cookie_name(request, LOGIN_COOKIE),
        token,
        httponly=True,
        samesite="Strict",
        secure=settings.secure_cookie,
        max_age=900,
    )
    return response


async def login_post(request: web.Request) -> web.Response:
    check_origin(request)
    state = request.app[STATE]
    data = await request.post()
    token = str(data.get("csrf", ""))
    if not hmac.compare_digest(
        token, request.cookies.get(cookie_name(request, LOGIN_COOKIE), "")
    ) or not valid_login_token(state.settings, token):
        raise web.HTTPForbidden(text="Форма входа устарела. Откройте /login заново.")
    peer = request.remote or "local"
    bucket = sign(state.settings, "login-peer:" + peer)
    # RU: Лимит хранится в БД и не сбрасывается перезапуском HTTP-сервиса.
    # EN: Serialize a bounded login bucket; never trust X-Forwarded-For by default.
    async with PostgresUnitOfWork(state.engine) as uow:
        await uow.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(bucket[:15], 16)}
        )
        rows = await uow.store.find(AdminLoginLimit, key=bucket)
        limit = rows[0] if rows else AdminLoginLimit(key=bucket, window_start=now())
        if not rows:
            uow.store.add(limit)
        if limit.window_start < now() - timedelta(minutes=15):
            limit.window_start, limit.attempts = now(), 0
        blocked = limit.attempts >= 6
        if not blocked:
            limit.attempts += 1
    if blocked:
        return web.Response(
            status=429, text="Слишком много попыток входа. Повторите через 15 минут."
        )
    password = str(data.get("password", ""))
    async with state.password_slots:
        matches = await asyncio.to_thread(
            password_matches, password, state.settings.password_hash.get_secret_value()
        )
    username = str(data.get("username", ""))
    if not (matches and hmac.compare_digest(username.encode(), state.settings.username.encode())):
        return web.Response(
            status=401,
            text=v.login(state.settings.username, token, "Неверный логин или пароль."),
            content_type="text/html",
        )
    raw = secrets.token_hex(32)
    async with PostgresUnitOfWork(state.engine) as uow:
        uow.store.add(
            AdminSession(
                token_hash=sign(state.settings, "session:" + raw),
                actor=state.settings.username,
                created_at=now(),
                expires_at=now() + SESSION_LIFETIME,
            )
        )
        uow.store.add(
            AdminAudit(
                actor=state.settings.username,
                action="login",
                target="web",
                request_key=secrets.token_hex(32),
            )
        )
        for expired in await uow.store.expired(AdminSession, "expires_at", now()):
            await uow.store.delete(expired)
        limits = await uow.store.find(AdminLoginLimit, key=bucket)
        if limits:
            limits[0].attempts = 0
    response = web.HTTPSeeOther("/")
    response.set_cookie(
        cookie_name(request),
        raw,
        httponly=True,
        samesite="Strict",
        secure=state.settings.secure_cookie,
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )
    response.del_cookie(cookie_name(request, LOGIN_COOKIE))
    return response


async def overview_data(state: State) -> dict[str, Any]:
    async with state.cache_lock:
        if time.monotonic() > state.cache_until:
            state.cache = await asyncio.wait_for(state.queries.overview(), timeout=8)
            state.cache_until = time.monotonic() + 20
        return dict(state.cache)


async def overview(request: web.Request) -> web.Response:
    state = request.app[STATE]
    data = await overview_data(state)
    metrics = "".join(
        v.metric(label, data[key], hint, key)
        for key, label, hint in [
            ("users", "Пользователи", "Все аккаунты бота"),
            ("groups", "Коллективы", "Без удалённых"),
            ("support", "Открытые обращения", "Ожидают ответа поддержки"),
            ("failed", "Ошибки отправки", "Не завершённые отправки"),
            ("concerts", "Предстоящие выступления", "Сегодня и позднее"),
            ("updates", "Обновления за 24 часа", "Учёт после установки мониторинга"),
            ("deliveries", "Доставлено за 24 часа", "Подтверждённые отправки"),
            ("pending", "В очереди", "С учётом отложенных повторов"),
        ]
    )
    activity = await state.queries.activity()
    from datetime import UTC, datetime

    values = {row["hour"]: row["total"] for row in activity}
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    series = [values.get(hour - timedelta(hours=i), 0) for i in reversed(range(24))]
    peak = max(series + [1])
    bars = "".join(
        f'<div class="chart-bar level-{max(0, min(10, round(n / peak * 10)))}" title="{n} обновлений"></div>'
        for n in series
    )
    chart = f'<section class="panel"><div class="section-heading"><h2>Активность бота</h2>{v.badge("Последние 24 часа")}</div><div class="chart">{bars}</div><div class="chart-labels"><span>24 часа назад</span><span>Сейчас</span></div><p class="hint">Это обновления Telegram, а не уникальные посетители. Исторические строки без даты не учитываются.</p></section>'
    health = f'<section class="panel"><h2>Хранилище и база</h2><dl class="meta"><dt>PostgreSQL</dt><dd>{v.badge("Доступна", "good")}</dd><dt>Размер базы</dt><dd>{v.bytes_label(data["database_bytes"])}</dd><dt>Таблица PDF</dt><dd>{v.bytes_label(data["pdf_storage_bytes"])}</dd><dt>Соединения с БД</dt><dd>{v.h(data["connections"])}</dd><dt>Миграция</dt><dd class="uuid">{v.h(data["migration"])}</dd><dt>Личные черновики</dt><dd>{v.h(data["drafts"])} · только количество</dd></dl></section>'
    return render(
        request,
        "Обзор системы",
        '<div class="metrics">'
        + metrics
        + '</div><div class="grid">'
        + chart
        + health
        + '</div><section class="panel"><h2>Рабочие разделы</h2><p class="hint">Обращения и отложенные отправки требуют внимания в первую очередь. Статус процесса и ошибки фоновых задач доступны в мониторинге.</p><div class="inline-actions">'
        + v.link("/support", "Открыть поддержку →")
        + " "
        + v.link("/queue?state=failed", "Проверить ошибки →")
        + "</div></section>",
        "overview",
    )


async def api_overview(request: web.Request) -> web.Response:
    return web.json_response(await overview_data(request.app[STATE]))


LIST_TITLES = {
    "users": "Пользователи",
    "groups": "Коллективы",
    "concerts": "Выступления",
    "queue": "Очередь отправки",
    "support": "Поддержка",
    "audit": "Журнал действий",
}


async def listing(request: web.Request) -> web.Response:
    section = request.match_info["section"]
    if section not in LIST_TITLES:
        raise web.HTTPNotFound()
    query = request.query.get("q", "")[:200]
    page_number = int(request.query.get("page", "0"))
    if not 0 <= page_number <= 4000:
        raise web.HTTPBadRequest(text="Недопустимая страница")
    state_filter = request.query.get("state", "")
    group_id = UUID(request.query["group"]) if "group" in request.query else None
    rows = await request.app[STATE].queries.listing(
        section, query=query, page=page_number, state=state_filter, group_id=group_id
    )
    has_next = len(rows) > PAGE_SIZE
    choices = {
        "groups": [("", "Все"), ("active", "Действующие"), ("deleted", "В корзине")],
        "concerts": [
            ("", "Все"),
            ("approved", "Утверждённые"),
            ("working", "Не утверждены"),
            ("deleted", "В корзине"),
        ],
        "queue": [
            ("", "Ожидают"),
            ("failed", "С ошибкой"),
            ("processed", "Обработанные"),
            ("all", "Все"),
        ],
        "support": [("", "Открытые"), ("closed", "Закрытые"), ("all", "Все")],
    }.get(section, [])
    output = []
    for row in rows[:PAGE_SIZE]:
        identifier = str(row["id"])
        if section == "users":
            output.append(
                [
                    v.link("/users/" + identifier, row["display_name"] or "Без имени"),
                    v.h(row["telegram_id"]),
                    v.h(row["language"]),
                    v.h(row["groups"]),
                    v.stamp(row["last_seen_at"]) if row["last_seen_at"] else "Нет данных",
                ]
            )
        elif section == "groups":
            output.append(
                [
                    v.link("/groups/" + identifier, row["name"]),
                    v.h(row["owner_name"]),
                    v.h(row["members"]),
                    v.h(row["concerts"]),
                    v.badge(
                        "В корзине" if row["deleted_at"] else "Действует",
                        "warn" if row["deleted_at"] else "good",
                    ),
                ]
            )
        elif section == "concerts":
            output.append(
                [
                    v.link(
                        "/concerts/" + identifier,
                        str(row["date"]) + " · " + (row["title"] or "Без названия"),
                    ),
                    v.link("/groups/" + str(row["group_id"]), row["group_name"]),
                    v.h(row["city"]),
                    v.badge(
                        "Утверждён" if row["approved"] else "В работе",
                        "good" if row["approved"] else "warn",
                    ),
                    v.h(row["version"]),
                ]
            )
        elif section == "queue":
            status, severity = v.delivery_status(row)
            controls = ""
            if not row["sent"]:
                checked = '<label><input type="checkbox" name="confirmed" value="yes" required>Подтверждаю действие</label>'
                controls = action_form(
                    request, "queue_retry", identifier, checked, "Повторить сейчас"
                ) + action_form(
                    request, "queue_discard", identifier, checked, "Исключить из отправки", True
                )
            output.append(
                [
                    v.stamp(row["created_at"]),
                    v.h(row["chat_id"]),
                    v.badge(status, severity),
                    v.h(row["attempts"]) + "<small>" + v.h(row["last_error"]) + "</small>",
                    v.h(row["preview"]),
                    v.stamp(row["next_attempt_at"]) if row["next_attempt_at"] else "—",
                    controls,
                ]
            )
        elif section == "support":
            output.append(
                [
                    v.link("/support/" + identifier, row["display_name"] or row["telegram_id"]),
                    v.h(row["telegram_id"]),
                    v.stamp(row["created_at"]),
                    v.badge(
                        "Открыто" if row["status"] == "open" else "Закрыто",
                        "warn" if row["status"] == "open" else "good",
                    ),
                    v.h(row["preview"]),
                ]
            )
        else:
            output.append(
                [
                    v.stamp(row["created_at"]),
                    v.h(row["actor"]),
                    v.h(row["source"]),
                    v.h(row["action"]),
                    v.h(row["target"]),
                    v.h(json.dumps(row["details"], ensure_ascii=False)),
                ]
            )
    headers = {
        "users": ["Пользователь", "Telegram ID", "Язык", "Коллективы", "Последняя активность"],
        "groups": ["Коллектив", "Владелец", "Участники", "Выступления", "Состояние"],
        "concerts": ["Выступление", "Коллектив", "Город", "Сет-лист", "Версия"],
        "queue": [
            "Создано",
            "Получатель",
            "Статус",
            "Попытки / ошибка",
            "Сообщение",
            "Следующий повтор",
            "Действия",
        ],
        "support": ["Пользователь", "Telegram ID", "Создано", "Статус", "Обращение"],
        "audit": ["Время", "Автор", "Источник", "Действие", "Объект", "Изменения"],
    }[section]
    return render(
        request,
        LIST_TITLES[section],
        v.search(query, state_filter, choices, {"group": str(group_id)} if group_id else None)
        + v.table(headers, output)
        + v.pagination(
            "/" + section,
            query,
            state_filter,
            page_number,
            has_next,
            {"group": str(group_id)} if group_id else None,
        ),
        section,
    )


async def user_detail(request: web.Request) -> web.Response:
    queries = request.app[STATE].queries
    identifier = UUID(request.match_info["identifier"])
    user = await queries.user(identifier)
    if not user:
        raise web.HTTPNotFound()
    page_number = max(0, min(4000, int(request.query.get("page", "0"))))
    rows = await queries.memberships(user=identifier, page=page_number)
    content = (
        '<section class="panel"><dl class="meta"><dt>Telegram ID</dt><dd>'
        + v.h(user["telegram_id"])
        + "</dd><dt>Язык</dt><dd>"
        + v.h(user["language"])
        + '</dd></dl><p class="hint">Личная переписка и содержимое черновиков недоступны в этом разделе.</p></section>'
    )
    content += v.table(
        ["Коллектив", "Имя в коллективе", "Права", "Статус"],
        [
            [
                v.link("/groups/" + str(row["group_id"]), row["name"]),
                v.h(row["nickname"]),
                v.h("владелец" if row["owner_id"] == identifier else row["role"]),
                v.h(row["status"]),
            ]
            for row in rows[:PAGE_SIZE]
        ],
    )
    return render(
        request,
        user["display_name"] or "Пользователь",
        content
        + v.pagination("/users/" + str(identifier), "", "", page_number, len(rows) > PAGE_SIZE),
        "users",
    )


async def group_detail(request: web.Request) -> web.Response:
    queries = request.app[STATE].queries
    identifier = UUID(request.match_info["identifier"])
    group = await queries.group(identifier)
    if not group:
        raise web.HTTPNotFound()
    query = request.query.get("q", "")[:200]
    page_number = max(0, min(4000, int(request.query.get("page", "0"))))
    members = await queries.memberships(group=identifier, query=query, page=page_number)
    body = (
        '<section class="panel"><dl class="meta"><dt>Владелец</dt><dd>'
        + v.h(group["owner_name"])
        + "</dd><dt>Telegram-чат</dt><dd>"
        + v.h(group["chat_id"])
        + "</dd><dt>Напоминания</dt><dd>"
        + v.badge("Включены" if group["reminders_enabled"] else "Выключены")
        + "</dd><dt>Состояние</dt><dd>"
        + v.badge("В корзине" if group["deleted_at"] else "Действует")
        + "</dd></dl>"
    )
    if group["deleted_at"]:
        body += action_form(
            request,
            "restore_group",
            identifier,
            f'<input type="hidden" name="epoch" value="{group["lifecycle_version"]}"><label><input type="checkbox" name="confirmed" value="yes" required>Восстановить коллектив из корзины</label>',
            "Восстановить коллектив",
        )
    if not group["deleted_at"]:
        enabled = "no" if group["reminders_enabled"] else "yes"
        body += action_form(
            request,
            "group_reminders",
            identifier,
            f'<input type="hidden" name="enabled" value="{enabled}">',
            "Выключить напоминания" if enabled == "no" else "Включить напоминания",
        )
    body += (
        '<div class="inline-actions">'
        + v.link("/concerts?group=" + str(identifier), "Выступления →")
        + " "
        + v.link("/groups/" + str(identifier) + "/catalog", "Каталог →")
        + "</div></section><h2>Участники</h2>"
        + v.search(query, "", [])
    )
    body += v.table(
        ["Участник", "Имя в коллективе", "Права", "Статус", "Управление"],
        [
            [
                v.link("/users/" + str(row["user_id"]), row["display_name"] or row["telegram_id"]),
                v.h(row["nickname"]),
                v.h("владелец" if row["user_id"] == group["owner_id"] else row["role"]),
                v.h(row["status"]),
                v.link(f"/groups/{identifier}/owner?member={row['id']}", "Передать владение")
                if row["status"] == "active"
                and row["user_id"] != group["owner_id"]
                and not group["deleted_at"]
                else "—",
            ]
            for row in members[:PAGE_SIZE]
        ],
    )
    body += v.pagination(
        "/groups/" + str(identifier), query, "", page_number, len(members) > PAGE_SIZE
    )
    return render(request, group["name"], body, "groups")


async def owner_form(request: web.Request) -> web.Response:
    queries = request.app[STATE].queries
    identifier = UUID(request.match_info["identifier"])
    group = await queries.group(identifier)
    member = await queries.one(
        "SELECT m.id,m.musical_role,u.display_name,u.telegram_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.id=:id AND m.group_id=:group AND m.status='active'",
        id=UUID(request.query["member"]),
        group=identifier,
    )
    if not group or not member or group["deleted_at"]:
        raise web.HTTPNotFound()
    body = (
        '<section class="panel"><h2>Передача владения через поддержку</h2>'
        + "<p>Коллектив: "
        + v.h(group["name"])
        + " · чат "
        + v.h(group["chat_id"])
        + "</p>"
        + "<p>Прежний владелец: "
        + v.h(group["owner_name"])
        + "</p>"
        + "<p>Новый владелец (Telegram ID "
        + v.h(member["telegram_id"])
        + "): <strong>"
        + v.h(member["musical_role"] or member["display_name"])
        + '</strong></p><p class="hint">Применяйте только после проверки обращения. Новый владелец сможет управлять администраторами. Прежний остаётся администратором.</p>'
    )
    fields = f'<input type="hidden" name="member_id" value="{v.h(member["id"])}"><input type="hidden" name="expected_owner" value="{v.h(group["owner_id"])}"><label>Введите точное название коллектива: {v.h(group["name"])}<input name="confirmation" maxlength="200" required autocomplete="off"></label>'
    body += (
        action_form(request, "group_owner", identifier, fields, "Подтвердить передачу", True)
        + "</section>"
    )
    return render(request, "Смена владельца", body, "groups")


async def catalog(request: web.Request) -> web.Response:
    identifier = UUID(request.match_info["identifier"])
    queries = request.app[STATE].queries
    group = await queries.group(identifier)
    if not group:
        raise web.HTTPNotFound()
    query = request.query.get("q", "")[:200]
    page_number = max(0, min(4000, int(request.query.get("page", "0"))))
    rows = await queries.listing("catalog", group_id=identifier, query=query, page=page_number)
    body = v.search(query, "", []) + v.table(
        ["Название", "Время", "BPM", "Свет", "Комментарий", "Архив"],
        [
            [
                v.h(row["title"]),
                v.h(clock(row["duration_seconds"])),
                v.h(row["bpm"]),
                v.h(row["lights"]),
                v.h(row["comment"]),
                v.h("Да" if row["archived"] else ""),
            ]
            for row in rows[:PAGE_SIZE]
        ],
    )
    return render(
        request,
        group["name"] + " · Каталог",
        body
        + v.pagination(
            "/groups/" + str(identifier) + "/catalog", query, "", page_number, len(rows) > PAGE_SIZE
        ),
        "groups",
    )


async def concert_detail(request: web.Request) -> web.Response:
    queries = request.app[STATE].queries
    concert = await queries.concert(UUID(request.match_info["identifier"]))
    if not concert:
        raise web.HTTPNotFound()
    snapshot = flat_content(await queries.shared_snapshot(concert))
    items = snapshot["blocks"][0]["items"]
    body = (
        '<section class="panel"><dl class="meta"><dt>Коллектив</dt><dd>'
        + v.link("/groups/" + str(concert["group_id"]), concert["group_name"])
        + "</dd><dt>Город</dt><dd>"
        + v.h(concert["city"])
        + "</dd><dt>Одобрение</dt><dd>"
        + v.badge(
            snapshot.get("approved_by", "Утверждён") if concert["approved"] else "В работе",
            "good" if concert["approved"] else "warn",
        )
        + "</dd><dt>Версия</dt><dd>"
        + v.h(concert["version"])
        + "</dd><dt>Общее время</dt><dd>"
        + clock(totals(snapshot)[2])
        + "</dd></dl></section>"
    )
    if concert["deleted_at"]:
        body += (
            '<section class="panel"><h2>Выступление в корзине</h2>'
            + action_form(
                request,
                "restore_concert",
                concert["id"],
                f'<input type="hidden" name="epoch" value="{concert["lifecycle_version"]}"><label><input type="checkbox" name="confirmed" value="yes" required>Подтверждаю восстановление</label>',
                "Восстановить выступление",
            )
            + "</section>"
        )
    body += v.table(
        ["№", "Песня / пауза", "Время", "BPM", "Свет", "Комментарий"],
        [
            [
                v.h(number),
                v.h(item["title"]),
                v.h(clock(item["duration_seconds"])),
                v.h(item.get("bpm")),
                v.h(item.get("lights", "")),
                v.h(item.get("comment", "")),
            ]
            for number, item in zip(entry_labels(items), items, strict=True)
        ],
    )
    return render(
        request,
        str(concert["date"]) + " · " + (concert["title"] or "Выступление"),
        body,
        "concerts",
    )


async def support_detail(request: web.Request) -> web.Response:
    queries = request.app[STATE].queries
    identifier = UUID(request.match_info["identifier"])
    case = await queries.ticket(identifier)
    if not case:
        raise web.HTTPNotFound()
    replies = await queries.replies(identifier)
    version_field = f'<input type="hidden" name="expected_version" value="{case["version"]}">'
    body = (
        '<div class="grid"><section class="panel"><h2>Обращение</h2><p class="hint">'
        + v.stamp(case["created_at"])
        + '</p><div class="pre">'
        + v.h(case["text"])
        + '</div></section><section class="panel"><h2>Ответить через бота</h2><p class="hint">Ответ будет отправлен в личный чат пользователя. Это не массовая рассылка.</p>'
    )
    body += action_form(
        request,
        "support_reply",
        identifier,
        version_field
        + '<label>Сообщение<textarea name="message" rows="7" maxlength="4000" required></textarea></label><label><input type="checkbox" name="close" value="yes">Закрыть обращение после ответа</label>',
        "Отправить ответ",
    )
    body += (
        action_form(
            request,
            "support_close" if case["status"] == "open" else "support_reopen",
            identifier,
            version_field,
            "Закрыть без ответа" if case["status"] == "open" else "Открыть снова",
        )
        + '</section></div><section class="panel"><h2>Ответы и доставка</h2>'
    )
    for row in replies:
        status, severity = v.delivery_status(row)
        body += (
            '<article class="reply">'
            + v.badge(status, severity)
            + " <small>"
            + v.stamp(row["created_at"])
            + " · "
            + v.h(row["actor"] or "Автор до обновления неизвестен")
            + " · "
            + v.h(row["source"])
            + '</small><div class="pre">'
            + v.h(row["text"])
            + "</div></article>"
        )
    body += (
        '<p class="hint">Записанных ответов нет. Старые ответы до установки админки могут не иметь привязки к обращению.</p>'
        if not replies
        else ""
    ) + "</section>"
    return render(request, "Обращение в поддержку", body, "support")


async def runtime(request: web.Request) -> web.Response:
    state = request.app[STATE]
    rows = await state.queries.runtime()
    rendered = []
    for row in rows:
        stale = now() - row["updated_at"] > timedelta(seconds=100)
        if row["component"] not in ("telegram", "bot"):
            deadline = row["next_run"] or row["last_activity_at"]
            stale = stale or (deadline is not None and now() - deadline > timedelta(seconds=100))
        rendered.append(
            [
                v.h(v.COMPONENT_NAMES.get(row["component"], row["component"])),
                v.badge(
                    "Нет свежего сигнала"
                    if stale
                    else v.STATE_NAMES.get(row["state"], row["state"]),
                    "bad"
                    if stale or row["state"] in ("retrying", "error", "polling_error", "stopped")
                    else "good",
                ),
                v.stamp(row["updated_at"]),
                v.h(row["errors"]),
                v.h(row["last_error"]),
                v.stamp(row["next_run"]) if row["next_run"] else "—",
                v.stamp(row["last_success_at"]) if row["last_success_at"] else "—",
                v.stamp(row["last_error_at"]) if row["last_error_at"] else "—",
            ]
        )
    body = (
        '<section class="panel"><h2>Фоновые процессы</h2><p class="hint">Сигнал обновляется примерно каждые 30 секунд. Живой процесс не гарантирует доступность Telegram; смотрите также очередь и последние входящие обновления.</p>'
        + v.table(
            [
                "Компонент",
                "Состояние",
                "Сигнал",
                "Ошибки за запуск",
                "Последняя ошибка",
                "Следующий запуск",
                "Последний успех",
                "Время ошибки",
            ],
            rendered,
        )
        + "</section>"
    )
    identity: dict[str, Any] = next(
        (row["details"] for row in rows if row["component"] == "bot"), {}
    )
    identity_labels = {
        "bot_username": "Бот",
        "bot_id": "Telegram ID",
        "environment": "Контур",
        "location": "Место запуска",
        "revision": "Версия кода",
        "schema": "Схема при запуске",
        "started_at": "Запущен (UTC)",
        "instance_id": "Экземпляр",
    }
    identity_panel = (
        '<section class="panel"><h2>Запущенный экземпляр</h2><dl class="meta">'
        + "".join(
            f"<dt>{label}</dt><dd>{v.h(identity.get(key, 'Неизвестно'))}</dd>"
            for key, label in identity_labels.items()
        )
        + f"<dt>Схема для этого кода админки</dt><dd>{SCHEMA_REVISION}</dd><dt>Версия админки</dt><dd>{v.h(state.revision)}</dd></dl></section>"
    )
    data = await overview_data(state)
    body = (
        identity_panel
        + body
        + '<section class="panel"><h2>Ожидание в очереди</h2><p>Самое старое ожидающее сообщение: '
        + str(max(0, int(data["oldest_pending_age_seconds"])))
        + "сек.</p></section>"
    )
    host: dict[str, Any] = {}
    try:
        path = Path(state.settings.host_report)
        if path.stat().st_size <= 65536:
            content = json.loads(path.read_text())
            if isinstance(content, dict):
                host = {
                    key: content[key]
                    for key in (
                        "collected_at",
                        "disk_free_bytes",
                        "disk_total_bytes",
                        "memory_available_bytes",
                        "memory_total_bytes",
                        "load_1m",
                        "last_backup",
                        "backup_age_seconds",
                        "backup_bytes",
                        "backup_timer",
                    )
                    if key in content
                }
    except (OSError, ValueError):
        pass
    if host:
        body += v.host_panel(host, state.settings.contour)
    else:
        body += '<section class="panel"><h2>Сервер и резервные копии</h2><p class="hint">Сборщик отчёта хоста не подключён. Админка не имеет доступа к Docker socket и полным дампам. Настройка описана в docs/web-admin.md.</p></section>'
    body += (
        '<section class="panel"><h2>Сессии админки</h2><p class="hint">Каждый вход действует до 8 часов. Можно завершить все сессии, например после потери устройства.</p>'
        + action_form(
            request,
            "revoke_sessions",
            "session",
            '<label><input type="checkbox" name="confirmed" value="yes" required>Завершить все входы, включая текущий</label>',
            "Завершить все сессии",
            True,
        )
        + "</section>"
    )
    return render(request, "Мониторинг", body, "runtime")


async def action(request: web.Request) -> web.Response:
    check_origin(request)
    state = request.app[STATE]
    action_name = request.match_info["action"]
    if state.settings.read_only and action_name not in ("logout", "revoke_sessions"):
        raise web.HTTPForbidden(text="Этот контур открыт только для просмотра.")
    target = request.match_info["target"]
    fields = await request.post()
    values = {str(key): str(value) for key, value in fields.items()}
    nonce, proof = values.get("nonce", ""), values.get("proof", "")
    if (
        not re.fullmatch("[a-f0-9]{32}", nonce) or not re.fullmatch("[a-f0-9]{64}", proof)
    ) or not hmac.compare_digest(
        proof,
        form_proof(
            state.settings, request.cookies[cookie_name(request)], action_name, target, nonce
        ),
    ):
        raise web.HTTPForbidden(text="Подпись формы неверна. Обновите страницу.")
    allowed = {
        "support_reply",
        "support_close",
        "support_reopen",
        "queue_retry",
        "queue_discard",
        "group_reminders",
        "group_owner",
        "logout",
        "revoke_sessions",
        "restore_group",
        "restore_concert",
    }
    if action_name not in allowed:
        raise web.HTTPNotFound()
    if state.settings.contour == "production" and action_name not in ("logout", "revoke_sessions"):
        processes = await state.queries.runtime()
        compatible = any(
            row["component"] == "bot"
            and row["details"].get("schema") == SCHEMA_REVISION
            and row["details"].get("environment") == "production"
            and row["details"].get("bot_id") == 7430933141
            and row["state"] != "stopped"
            and now() - row["updated_at"] < timedelta(seconds=100)
            for row in processes
        )
        if not compatible:
            raise web.HTTPConflict(
                text="Изменения недоступны: основной бот ещё не обновлён или нет свежего сигнала совместимой версии."
            )
    request_key = digest(request.cookies[cookie_name(request)] + ":" + nonce)
    actor = auth(request).actor
    try:
        async with PostgresUnitOfWork(state.engine) as uow:
            if action_name in ("logout", "revoke_sessions"):
                if target != "session" or (
                    action_name == "revoke_sessions" and values.get("confirmed") != "yes"
                ):
                    raise web.HTTPBadRequest()
                uow.store.add(
                    AdminAudit(
                        actor=actor, action=action_name, target="web", request_key=request_key
                    )
                )
                sessions = (
                    await uow.store.find(AdminSession, actor=actor)
                    if action_name == "revoke_sessions"
                    else await uow.store.find(AdminSession, id=auth(request).id)
                )
                for session in sessions:
                    await uow.store.delete(session)
            else:
                await Administration(uow.store, actor).execute(
                    action_name, UUID(target), values, request_key
                )
    except IntegrityError as exc:
        # Only the one-time request key denotes a duplicate; other constraint errors are failures.
        if "uq_admin_audit_request_key" not in str(
            getattr(exc.orig, "__cause__", "")
        ) and "uq_admin_audit_request_key" not in str(exc.orig):
            raise
        return web.HTTPSeeOther("/audit?notice=duplicate")
    state.cache_until = 0
    if action_name in ("logout", "revoke_sessions"):
        response = web.HTTPSeeOther("/login")
        response.del_cookie(cookie_name(request))
        return response
    destination = (
        "/support/" + target
        if action_name.startswith("support_")
        else "/groups/" + target
        if action_name.startswith("group_")
        else "/groups/" + target
        if action_name == "restore_group"
        else "/concerts/" + target
        if action_name == "restore_concert"
        else "/queue"
    )
    return web.HTTPSeeOther(
        destination + "?notice=" + ("queued" if action_name == "support_reply" else "saved")
    )


async def health(request: web.Request) -> web.Response:
    try:
        await request.app[STATE].queries.rows("SELECT id FROM admin_sessions LIMIT 1")
    except Exception:
        return web.json_response({"status": "unavailable"}, status=503)
    return web.json_response({"status": "ok"})


def create_app(settings: AdminSettings, engine: AsyncEngine | None = None) -> web.Application:
    url = settings.checked_url()
    external_engine = engine is not None
    engine = engine or create_async_engine(
        url,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args={"server_settings": {"statement_timeout": "8000"}, "timeout": 5},
    )
    app = web.Application(middlewares=[boundary, authenticate], client_max_size=32768)
    app[STATE] = State(settings, engine, AdminQueries(engine), external_engine)
    app.router.add_get("/healthz", health)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_get("/", overview)
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/runtime", runtime)
    app.router.add_get("/groups/{identifier}/owner", owner_form)
    app.router.add_get("/groups/{identifier}/catalog", catalog)
    app.router.add_get("/users/{identifier}", user_detail)
    app.router.add_get("/groups/{identifier}", group_detail)
    app.router.add_get("/concerts/{identifier}", concert_detail)
    app.router.add_get("/support/{identifier}", support_detail)
    app.router.add_get("/{section:users|groups|concerts|support|queue|audit}", listing)
    app.router.add_post("/actions/{action}/{target}", action)
    app.router.add_static(
        "/static/", Path(__file__).parent / "static", show_index=False, follow_symlinks=False
    )

    async def close(application: web.Application) -> None:
        if not application[STATE].external_engine:
            await application[STATE].engine.dispose()

    app.on_cleanup.append(close)
    return app
