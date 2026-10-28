"""One browser entry point, separate applications, pools, caches and sessions per contour."""

from aiohttp import web
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.admin.app import STATE, create_app
from setlist_bot.admin.security import AdminSettings, sign
from setlist_bot.infrastructure.runtime import check_schema


def create_console(
    settings: AdminSettings, engines: dict[str, AsyncEngine] | None = None
) -> web.Application:
    if settings.production_database_url is not None and not settings.allow_production:
        raise ValueError("Production connection requires WEB_ADMIN_ALLOW_PRODUCTION=true")
    if settings.production_database_url is not None:
        sandbox_url = make_url(settings.database_url.get_secret_value())
        production_url = make_url(settings.production_database_url.get_secret_value())
        if sandbox_url.database == production_url.database:
            raise ValueError("Sandbox and production must use different databases")
    console = web.Application()
    environments: list[tuple[str, SecretStr, bool]] = [("sandbox", settings.database_url, False)]
    if settings.production_database_url is not None:
        environments.append(
            ("production", settings.production_database_url, settings.production_read_only)
        )
    sandbox: web.Application | None = None
    for name, url, read_only in environments:
        contour = settings.model_copy(
            update={
                "database_url": url,
                "url_prefix": "/" + name,
                "contour": name,
                "allow_production": name == "production",
                "read_only": read_only,
                "host_report": settings.production_host_report
                if name == "production"
                else settings.host_report,
                "console_switch": True,
                "production_available": settings.production_database_url is not None,
                # RU: Подписи и сессии разделены, даже когда пароль одинаковый.
                # EN: Another tab cannot retarget an already opened mutation form.
                "secret": SecretStr(sign(settings, "console:" + name)),
            }
        )
        app = create_app(contour, (engines or {}).get(name))
        if name == "sandbox":
            sandbox = app
        console.add_subapp("/" + name, app)

    async def health(request: web.Request) -> web.Response:
        assert sandbox is not None
        try:
            await check_schema(sandbox[STATE].engine)
        except Exception:
            return web.json_response({"status": "unavailable"}, status=503)
        return web.json_response({"status": "ok"})

    async def switch(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name == "production" and settings.production_database_url is None:
            return web.Response(status=503, text="Основной контур ещё не подключён.")
        raise web.HTTPSeeOther("/" + name + "/")

    async def redirect(request: web.Request) -> web.Response:
        tail = request.match_info["tail"]
        if tail == "production" or tail.startswith("production/"):
            return web.Response(
                status=503,
                text="Основной контур ещё не подключён. "
                "Тестовая база не используется вместо основной.",
            )
        if request.method not in ("GET", "HEAD"):
            raise web.HTTPConflict(text="Адрес формы изменился. Откройте админку заново.")
        raise web.HTTPSeeOther("/sandbox/" + tail)

    console.router.add_get("/healthz", health)
    console.router.add_get("/_contour/{name:sandbox|production}", switch)
    console.router.add_route("*", "/{tail:.*}", redirect)
    return console
