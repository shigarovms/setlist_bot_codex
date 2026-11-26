import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qsl
from uuid import UUID

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from setlist_bot.application.importing import Importer
from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.miniapp_catalog import CATALOG_OPERATIONS, MiniCatalog
from setlist_bot.application.repertoire_import import RepertoireImport
from setlist_bot.application.workspace import PAGE_SIZE, Workspace
from setlist_bot.application.workspace_band_settings import WorkspaceBandSettings
from setlist_bot.application.workspace_band_trash import WorkspaceBandTrash
from setlist_bot.application.workspace_exports import WorkspaceExports
from setlist_bot.application.workspace_gigs import WorkspaceGigs
from setlist_bot.application.workspace_inbox import WorkspaceInbox
from setlist_bot.application.workspace_music import WorkspaceMusic
from setlist_bot.application.workspace_performances import WorkspacePerformances
from setlist_bot.application.workspace_profile import WorkspaceProfile
from setlist_bot.application.workspace_roster import WorkspaceRoster
from setlist_bot.application.workspace_setlists import WorkspaceSetlists
from setlist_bot.application.workspace_staffing import WorkspaceStaffing
from setlist_bot.application.workspace_team import WorkspaceTeam, text_field
from setlist_bot.application.workspace_timing import WorkspaceTiming
from setlist_bot.domain.entities import Confirmation, User, now
from setlist_bot.domain.miniapp_auth import verify_init_data
from setlist_bot.domain.rules import AppError, require
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.runtime import check_schema
from setlist_bot.miniapp.browser_login import (
    CALLBACK_PATH,
    FLOW_COOKIE,
    SESSION_COOKIE,
    BrowserLogin,
    browser_session,
    clear_cookie,
    start_session,
)
from setlist_bot.miniapp.button_labels import workspace_button_labels
from setlist_bot.miniapp.settings import MiniAppSettings
from setlist_bot.transport.help_texts import workspace_help
from setlist_bot.transport.i18n import tr

ENGINE = web.AppKey("engine", AsyncEngine)
SETTINGS = web.AppKey("settings", MiniAppSettings)
GATE = web.AppKey("gate", asyncio.Semaphore)
LANGUAGE = web.RequestKey("miniapp-language", str)
logger = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name("static")
INITIAL_THEME = (ASSETS / "initial-theme.js").read_text(encoding="utf-8")
INITIAL_THEME_HASH = base64.b64encode(hashlib.sha256(INITIAL_THEME.encode()).digest()).decode()
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        f"script-src 'self' https://telegram.org 'sha256-{INITIAL_THEME_HASH}'; "
        "style-src 'self'; "
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
            if exc.code in ("conflict", "selection_catalog_changed", "import_changed")
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
    if name == "index.html":
        html = (ASSETS / name).read_text(encoding="utf-8")
        html = html.replace(
            "<!-- initial theme -->", f'<script id="initial-theme">{INITIAL_THEME}</script>', 1
        )
        return web.Response(text=html, content_type="text/html", charset="utf-8")
    if name == "bot-labels-data.js":
        return web.Response(
            text=f"export const labels = {json.dumps(workspace_button_labels())};",
            content_type="text/javascript",
            charset="utf-8",
        )
    if name == "unbounded.ttf":
        return web.Response(body=(ASSETS / name).read_bytes(), content_type="font/ttf")
    if name == "unbounded-OFL.txt":
        return web.Response(
            body=(ASSETS / name).read_bytes(), content_type="text/plain", charset="utf-8"
        )
    types = {
        "app.js": "text/javascript",
        "modal-scroll.js": "text/javascript",
        "bot-labels.js": "text/javascript",
        "workspace.js": "text/javascript",
        "repertoire-import.js": "text/javascript",
        "repertoire-music.js": "text/javascript",
        "shared-setlist.js": "text/javascript",
        "workspace-export.js": "text/javascript",
        "workspace-performances.js": "text/javascript",
        "workspace-timing.js": "text/javascript",
        "workspace-band-settings.js": "text/javascript",
        "workspace-band-trash.js": "text/javascript",
        "workspace-team.js": "text/javascript",
        "workspace-staffing.js": "text/javascript",
        "workspace-roster.js": "text/javascript",
        "workspace-inbox.js": "text/javascript",
        "workspace-gigs.js": "text/javascript",
        "workspace-schedule.js": "text/javascript",
        "workspace-profile.js": "text/javascript",
        "workspace-help.js": "text/javascript",
        "workspace-request-dialog.js": "text/javascript",
        "model.js": "text/javascript",
        "persistence.js": "text/javascript",
        "style.css": "text/css",
    }
    if name not in types:
        raise web.HTTPNotFound()
    return web.Response(
        body=(ASSETS / name).read_bytes(), content_type=types[name], charset="utf-8"
    )


async def login_config(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS]
    return web.json_response(
        {
            "enabled": settings.browser_login_secret is not None,
            "bot_url": f"https://t.me/{settings.bot_username}",
        }
    )


async def login_begin(request: web.Request) -> web.Response:
    invitation = request.query.get("invitation")
    return BrowserLogin(request.app[SETTINGS]).begin(UUID(invitation) if invitation else None)


async def login_callback(request: web.Request) -> web.Response:
    login = BrowserLogin(request.app[SETTINGS])
    gate = request.app[GATE]
    if gate.locked():
        raise web.HTTPServiceUnavailable()
    async with gate:
        try:
            actor, invitation = await login.verify_callback(request)
        except AppError:
            response = web.Response(status=303, headers={"Location": "/miniapp/?login=failed"})
        else:
            async with PostgresUnitOfWork(request.app[ENGINE]) as uow:
                users = await uow.store.find(User, telegram_id=actor)
                target = "/miniapp/" if users else "/miniapp/?login=start_bot"
                if users and invitation is not None:
                    target = f"/miniapp/?workspace=1&view=my-gigs&invitation={invitation}"
                response = web.Response(status=303, headers={"Location": target})
                if users:
                    start_session(uow.store, users[0], response)
        clear_cookie(response, FLOW_COOKIE, CALLBACK_PATH)
        return response


async def logout(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS]
    if (
        settings.browser_login_secret is None
        or request.headers.get("Origin") != settings.public_base_url
    ):
        raise AppError("miniapp_auth")
    gate = request.app[GATE]
    if gate.locked():
        raise web.HTTPServiceUnavailable()
    async with gate, PostgresUnitOfWork(request.app[ENGINE]) as uow:
        try:
            row = await browser_session(uow.store, request)
        except AppError:
            pass
        else:
            row.expires_at = now()
    response = web.json_response({"status": "ok"})
    clear_cookie(response, SESSION_COOKIE, "/miniapp/")
    return response


async def api(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS]
    operation = request.match_info["operation"]
    if request.method == "POST" and request.headers.get("Origin") != settings.public_base_url:
        raise AppError("miniapp_auth")
    header = request.headers.get("Authorization", "")
    miniapp_key = base64.urlsafe_b64decode(settings.auth_key.get_secret_value())
    actor = None
    if header.startswith("tma "):
        actor = verify_init_data(
            header[4:],
            miniapp_key,
            allow_group=True,
        )
    elif header or settings.browser_login_secret is None or not request.cookies.get(SESSION_COOKIE):
        raise AppError("miniapp_auth")
    if request.app[GATE].locked():
        return web.json_response(
            {"error": "busy", "message": "Попробуйте позже / Try again"}, status=503
        )
    async with request.app[GATE]:
        payload = await request.json() if request.method == "POST" else dict(request.query)
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        async with PostgresUnitOfWork(request.app[ENGINE]) as uow:
            if actor is None:
                browser = await browser_session(uow.store, request)
                user = await uow.store.get(User, browser.user_id)
            else:
                users = await uow.store.find(User, telegram_id=actor)
                if not users:
                    raise AppError("miniapp_auth")
                user = users[0]
            request[LANGUAGE] = user.language
            service = MiniDrafts(uow.store, user)
            if operation == "help" and request.method == "GET":
                return web.json_response(
                    {"language": user.language, "topics": workspace_help(user.language)}
                )
            if operation in ("open_account", "open_schedule") and request.method == "POST":
                account = MiniCatalog(service).open_account()
                return web.json_response({"session": str(account.id), "language": user.language})
            if operation == "profile" and request.method == "GET":
                return web.json_response(WorkspaceProfile(uow.store, user).view())
            if operation == "profile_roles" and request.method == "POST":
                return web.json_response(await WorkspaceProfile(uow.store, user).preview(payload))
            if operation == "my_gigs" and request.method == "GET":
                section = payload.get("section", "invitations")
                page = int(payload.get("page", "0"))
                if (
                    section not in ("invitations", "confirmed", "history", "schedule", "managed")
                    or not 0 <= page <= 10000
                ):
                    raise ValueError("Invalid personal invitation options")
                return web.json_response(
                    await WorkspaceGigs(uow.store, user).listing(section, page, PAGE_SIZE)
                )
            if operation in ("gig_card", "gig_setlist") and request.method == "GET":
                gigs = WorkspaceGigs(uow.store, user)
                invitation_id = UUID(payload["invitation"])
                data = (
                    await gigs.card(invitation_id)
                    if operation == "gig_card"
                    else await gigs.setlist(invitation_id)
                )
                return web.json_response(data)
            if operation == "open_gig" and request.method == "POST":
                guest = await MiniCatalog(service).open_gig(UUID(payload["invitation"]))
                return web.json_response({"session": str(guest.id)})
            if operation == "export_options" and request.method == "GET":
                if payload.get("invitation"):
                    scope = await WorkspaceGigs(uow.store, user).confirmed(
                        UUID(payload["invitation"])
                    )
                    guest_exports = WorkspaceExports(
                        uow.store, user, invitation_id=scope.invitation.id
                    )
                    return web.json_response(
                        await guest_exports.options(scope.group.id, scope.event.id)
                    )
                return web.json_response(
                    await WorkspaceExports(uow.store, user).options(
                        UUID(payload["band"]), UUID(payload["performance"])
                    )
                )
            if operation == "open_shared" and request.method == "POST":
                shared = await WorkspaceSetlists(uow.store, user).open(
                    UUID(payload["band"]), UUID(payload["performance"])
                )
                return web.json_response({"session": str(shared.id)})
            if (
                operation
                in (
                    "shared_editor",
                    "shared_variants",
                    "shared_variant",
                    "shared_requests",
                    "shared_history",
                )
                and request.method == "GET"
            ):
                shared_lists = WorkspaceSetlists(uow.store, user)
                group_id, concert_id = UUID(payload["band"]), UUID(payload["performance"])
                if operation == "shared_editor":
                    return web.json_response(await shared_lists.editor(group_id, concert_id))
                if operation == "shared_requests":
                    return web.json_response(await shared_lists.requests(group_id, concert_id))
                if operation == "shared_variant":
                    return web.json_response(
                        await shared_lists.variant(
                            group_id, concert_id, payload["kind"], UUID(payload["variant"])
                        )
                    )
                page = int(payload.get("page", "0"))
                if not 0 <= page <= 10000:
                    raise ValueError("Invalid page")
                return web.json_response(
                    await shared_lists.variants(
                        group_id, concert_id, page, history=operation == "shared_history"
                    )
                )
            if operation == "music_search" and request.method == "GET":
                result = await WorkspaceMusic(uow.store, user).view(UUID(payload["band"]))
                return web.json_response(result)
            if operation == "bands" and request.method == "GET":
                bands = await Workspace(uow.store, user).bands()
                bands["connect_url"] = (
                    f"https://t.me/{settings.bot_username}?startgroup=connect_band"
                )
                return web.json_response(bands)
            if operation == "band_settings" and request.method == "GET":
                group_id = UUID(payload["band"])
                band_settings = await WorkspaceBandSettings(uow.store, user).view(group_id)
                actions = band_settings.pop("chat_actions")
                bot_url = f"https://t.me/{settings.bot_username}"
                band_settings["telegram"] = {
                    "cleanup_url": f"{bot_url}?startgroup=cleanup_{group_id.hex}"
                    if actions["cleanup"]
                    else None,
                    "transfer_url": f"{bot_url}?start=transfer_{group_id.hex}"
                    if actions["transfer"]
                    else None,
                }
                return web.json_response(band_settings)
            if operation == "band_trash" and request.method == "GET":
                page = int(payload.get("page", "0"))
                if not 0 <= page <= 10000:
                    raise ValueError("Invalid page")
                return web.json_response(
                    await WorkspaceBandTrash(uow.store, user).view(page, PAGE_SIZE)
                )
            if operation == "inbox" and request.method == "GET":
                return web.json_response(await WorkspaceInbox(uow.store, user).view())
            if operation == "team_inbox" and request.method == "GET":
                return web.json_response(await WorkspaceTeam(uow.store, user).inbox())
            if operation == "team" and request.method == "GET":
                page = int(payload.get("page", "0"))
                if not 0 <= page <= 10000:
                    raise ValueError("Invalid page")
                return web.json_response(
                    await WorkspaceTeam(uow.store, user).view(
                        UUID(payload["band"]), page, PAGE_SIZE, own_only=payload.get("roles") == "1"
                    )
                )
            if operation == "team_contact" and request.method == "GET":
                return web.json_response(
                    await WorkspaceTeam(uow.store, user).contact(
                        UUID(payload["band"]), UUID(payload["contact"])
                    )
                )
            if operation == "team_recipient" and request.method == "POST":
                return web.json_response(
                    await WorkspaceTeam(uow.store, user).recipient(
                        UUID(payload["band"]), text_field(payload, "reference", 64)
                    )
                )
            if operation == "staff_directory" and request.method == "GET":
                section = payload.get("section")
                selected_record = UUID(payload["record"]) if payload.get("record") else None
                page, query = int(payload.get("page", "0")), payload.get("query", "").strip()
                if (
                    section not in ("contacts", "lineups")
                    or not 0 <= page <= 10000
                    or len(query) > 200
                ):
                    raise ValueError("Invalid directory options")
                return web.json_response(
                    await WorkspaceStaffing(uow.store, user).directory(
                        UUID(payload["band"]),
                        section,
                        selected_record,
                        query if selected_record is None else "",
                        page,
                        PAGE_SIZE,
                    )
                )
            if operation == "staff_lineup_preview" and request.method == "POST":
                return web.json_response(
                    await WorkspaceStaffing(uow.store, user).preview(UUID(payload["band"]), payload)
                )
            if operation == "performances" and request.method == "GET":
                past, page = payload.get("past", "0"), int(payload.get("page", "0"))
                if past not in ("0", "1") or not 0 <= page <= 10000:
                    raise ValueError("Invalid page")
                return web.json_response(
                    await Workspace(uow.store, user).performances(
                        UUID(payload["band"]), past=past == "1", page=page
                    )
                )
            if operation == "timing" and request.method == "GET":
                return web.json_response(
                    await WorkspaceTiming(uow.store, user).view(
                        UUID(payload["performance"]), UUID(payload["band"])
                    )
                )
            if operation == "performance_history" and request.method == "GET":
                page = int(payload.get("page", "0"))
                require(0 <= page <= 10000, "input")
                return web.json_response(
                    await WorkspacePerformances(uow.store, user).history(
                        UUID(payload["band"]),
                        page,
                        timing_for=UUID(payload["performance"])
                        if payload.get("performance")
                        else None,
                    )
                )
            if operation == "performance_options" and request.method == "GET":
                return web.json_response(
                    await WorkspacePerformances(uow.store, user).options(UUID(payload["band"]))
                )
            if operation == "performance" and request.method == "GET":
                return web.json_response(
                    await Workspace(uow.store, user).performance(
                        UUID(payload["band"]), UUID(payload["performance"])
                    )
                )
            if operation == "performance_roster" and request.method == "GET":
                roster = await WorkspaceRoster(uow.store, user).view(
                    UUID(payload["band"]), UUID(payload["performance"])
                )
                roster["bot_username"] = settings.bot_username
                return web.json_response(roster)
            if operation == "performance_trash" and request.method == "GET":
                page = int(payload.get("page", "0"))
                if not 0 <= page <= 10000:
                    raise ValueError("Invalid page")
                return web.json_response(
                    await WorkspacePerformances(uow.store, user).trash_page(
                        UUID(payload["band"]), page, PAGE_SIZE
                    )
                )
            if operation == "open_draft" and request.method == "POST":
                editor = await service.create(UUID(payload["band"]), UUID(payload["performance"]))
                return web.json_response({"session": str(editor.id)})
            if operation == "open_repertoire" and request.method == "POST":
                repertoire = await MiniCatalog(service).open_repertoire(UUID(payload["band"]))
                return web.json_response({"session": str(repertoire.id)})
            if operation == "open_team_membership" and request.method == "POST":
                member = await MiniCatalog(service).open_team_membership(
                    UUID(text_field(payload, "membership", 36))
                )
                return web.json_response({"session": str(member.id)})
            if operation == "repertoire_import_preview" and request.method == "POST":
                source = RepertoireImport.from_payload(payload)
                importer = Importer(uow.store, user, user.telegram_id)
                plan = await source.preview(importer, UUID(payload["band"]))
                table = plan.parsed.table
                first = (
                    table.rows[int(table.has_header)]
                    if len(table.rows) > int(table.has_header)
                    else []
                )
                return web.json_response(
                    {
                        "fingerprint": plan.fingerprint,
                        "ready": not (plan.parsed.error or plan.errors)
                        and bool(plan.new or plan.supplements),
                        "has_header": table.has_header,
                        "columns": [
                            value or (first[i] if first else "")
                            for i, value in enumerate(table.headers)
                        ],
                        "error": tr(user.language, plan.parsed.error) if plan.parsed.error else "",
                        "new": plan.new,
                        "duplicates": [row.title for row in plan.duplicates],
                        "supplements": [
                            {"title": row.title, "fields": row.fields} for row in plan.supplements
                        ],
                        "errors": [
                            {
                                "row": row.number,
                                "title": row.title,
                                "message": tr(user.language, row.error),
                            }
                            for row in plan.errors
                        ],
                    }
                )
            if operation == "repertoire_proposals" and request.method == "GET":
                page = int(payload.get("page", "0"))
                if not 0 <= page <= 10000:
                    raise ValueError("Invalid page")
                return web.json_response(
                    await Workspace(uow.store, user).proposals(UUID(payload["band"]), page)
                )
            if operation == "repertoire" and request.method == "GET":
                archived, page = payload.get("archived", "0"), int(payload.get("page", "0"))
                query = payload.get("query", "").strip()
                if archived not in ("0", "1") or not 0 <= page <= 10000 or len(query) > 200:
                    raise ValueError("Invalid catalog options")
                return web.json_response(
                    await Workspace(uow.store, user).repertoire(
                        UUID(payload["band"]),
                        archived=archived == "1",
                        page=page,
                        query=query,
                        song_id=UUID(payload["song"]) if payload.get("song") else None,
                    )
                )
            if operation == "launch" and request.method == "GET":
                # RU: Цель берётся только из проверенного Telegram start_param.
                # EN: Never authorize a target supplied through initDataUnsafe or URL alone.
                start = dict(parse_qsl(header[4:])).get("start_param", "")
                if actor is None or not start.startswith("sl_"):
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
            elif request.method == "GET" and operation == "draft_shared":
                _, _, concert = await service.load(identifier)
                shared_lists = WorkspaceSetlists(uow.store, user)
                await shared_lists.groups.access(concert.group_id, admin=True)
                result = await shared_lists.editor_state(concert.group_id, concert.id)
            elif request.method == "GET" and operation == "song":
                result = await MiniCatalog(service).read(identifier, UUID(payload["song_id"]))
            elif request.method == "POST" and operation in CATALOG_OPERATIONS:
                result = await MiniCatalog(service).queue(
                    identifier,
                    payload,
                    operation=operation,
                    bot_username=settings.bot_username,
                    miniapp_key=miniapp_key,
                    browser=actor is None,
                )
            elif request.method == "POST" and operation == "export_preview":
                from setlist_bot.infrastructure.pdf import PdfDocuments

                target = await MiniCatalog(service).target(identifier)
                require(
                    (target.shared or target.invitation_id is not None)
                    and target.concert is not None,
                    "miniapp_expired",
                )
                assert target.concert is not None and target.group_id is not None
                command = WorkspaceExports.command({**payload, "action": "generate"})
                result = await WorkspaceExports(
                    uow.store, user, invitation_id=target.invitation_id
                ).preview(target.group_id, target.concert.id, command, PdfDocuments())
            elif request.method == "GET" and operation == "export_download":
                scoped = MiniCatalog(service)
                target = await scoped.target(identifier)
                require(
                    (target.shared or target.invitation_id is not None)
                    and target.concert is not None,
                    "miniapp_expired",
                )
                assert target.concert is not None
                assert target.group_id is not None
                record = await scoped.status(identifier, UUID(payload["request"]))
                audience = payload.get("audience", record.get("audience"))
                require(audience in ("musicians", "lights"), "input")
                files = await WorkspaceExports(
                    uow.store, user, invitation_id=target.invitation_id
                ).downloads(target.group_id, target.concert.id, record, audience)
                (file,) = files
                return web.Response(
                    body=file.content,
                    content_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{file.filename}"'},
                )
            elif request.method == "GET" and operation == "catalog_status":
                result = await MiniCatalog(service).status(
                    identifier, UUID(payload["request"]), miniapp_key=miniapp_key
                )
            elif request.method == "GET" and operation == "history_menu":
                result = {"sources": await service.history_menu(identifier)}
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
                        update_group_default=payload.get("update_group_default", True),
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
                        approved_version=payload.get("approved_version"),
                        default_pause_seconds=payload.get("default_pause_seconds"),
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
        if result.get("error") and "message" not in result:
            result["message"] = tr(user.language, result["error"])
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
    # RU/EN: Up to 200 labels of 1000 Unicode characters, with a bounded request body.
    app = web.Application(middlewares=[errors], client_max_size=1024 * 1024)
    app[ENGINE], app[SETTINGS], app[GATE] = engine, settings, asyncio.Semaphore(4)
    app.router.add_get("/miniapp/", asset)
    app.router.add_get("/miniapp/{asset}", asset)
    app.router.add_route("GET", "/miniapp/api/{operation}", api)
    app.router.add_route("POST", "/miniapp/api/{operation}", api)
    app.router.add_get("/miniapp/auth/config", login_config)
    app.router.add_get("/miniapp/auth/login", login_begin, allow_head=False)
    app.router.add_get(CALLBACK_PATH, login_callback, allow_head=False)
    app.router.add_post("/miniapp/auth/logout", logout)
    app.router.add_get("/healthz", health)

    async def startup(app: web.Application) -> None:
        await identity(app[ENGINE], settings)

    async def cleanup(app: web.Application) -> None:
        if owned:
            await app[ENGINE].dispose()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app
