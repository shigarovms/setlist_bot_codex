"""Real Chromium smoke test; Telegram SDK is stubbed only in the test browser."""

import asyncio
import base64
import json
import shutil
import socket
import subprocess
from urllib.parse import urlencode

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from cryptography.fernet import Fernet
from test_miniapp import KEY, session, settings, signed

from setlist_bot.application.setlists import Setlists
from setlist_bot.application.support import Support
from setlist_bot.application.workspace_schedule import CalendarRuntime
from setlist_bot.domain.entities import DeploymentIdentity, Outbox
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.miniapp_catalog_delivery import deliver_catalog
from setlist_bot.miniapp.app import create_app


async def browser_tab(client, port, process):
    for _ in range(200):
        if process.poll() is not None:
            break
        try:
            async with client.get(f"http://127.0.0.1:{port}/json/list") as response:
                pages = await response.json()
            for page in pages:
                if page["type"] == "page" and page["url"] == "about:blank":
                    return page["webSocketDebuggerUrl"]
        except (aiohttp.ClientError, ValueError):
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"Chromium test tab did not open (exit code: {process.poll()})")


async def process_catalog_request(engine, *, calendar=None):
    for _ in range(50):
        async with PostgresUnitOfWork(engine) as uow:
            events = await uow.store.find(Outbox, support_kind="catalog", sent=False)
            if events:
                assert await deliver_catalog(uow, events[0], calendar=calendar)
                return
        await asyncio.sleep(0.1)
    pytest.fail("Catalog request did not reach the bot worker")


class Browser:
    def __init__(self, ws):
        self.ws, self.sequence, self.pending = ws, 0, {}
        self.font_request = None
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        async for message in self.ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(message.data)
            if "id" in data:
                future = self.pending.pop(data["id"])
                if "error" in data:
                    future.set_exception(RuntimeError(str(data["error"])))
                else:
                    future.set_result(data.get("result", {}))
            elif data.get("method") == "Fetch.requestPaused":
                if data["params"]["request"]["url"].endswith("/unbounded.ttf"):
                    self.font_request = data["params"]["requestId"]
                    continue
                asyncio.create_task(
                    self.call(
                        "Fetch.fulfillRequest",
                        {
                            "requestId": data["params"]["requestId"],
                            "responseCode": 200,
                            "responseHeaders": [
                                {"name": "Content-Type", "value": "text/javascript"}
                            ],
                            "body": "",
                        },
                    )
                )
            elif data.get("method") == "Page.javascriptDialogOpening":
                asyncio.create_task(self.call("Page.handleJavaScriptDialog", {"accept": True}))

    async def call(self, method, params=None):
        self.sequence += 1
        identifier = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        await self.ws.send_json({"id": identifier, "method": method, "params": params or {}})
        return await asyncio.wait_for(future, 10)

    async def js(self, expression):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        assert "exceptionDetails" not in result, result
        return result.get("result", {}).get("value")

    async def wait(self, expression):
        for _ in range(200):
            if await self.js(expression):
                return
            await asyncio.sleep(0.05)
        page = await self.js("({url: location.href, text: document.body?.innerText.slice(0, 500)})")
        raise AssertionError(f"Browser condition failed: {expression}. Page: {page}")

    async def click_named(self, selector, title):
        match = f"Array.from(document.querySelectorAll({json.dumps(selector)})).find("
        match += f"element => element.textContent.trim() === {json.dumps(title)})"
        await self.wait(f"!!({match})")
        await self.js(f"{match}.click()")

    async def assert_button_text_fits(self):
        overflow = await self.js("""
            [...document.querySelectorAll('button, .workspace-nav a, .toolbar a')]
                .filter(control => control.getClientRects().length && control.textContent.trim())
                .filter(control => {
                    const bounds = control.getBoundingClientRect();
                    const range = document.createRange();
                    range.selectNodeContents(control);
                    return [...range.getClientRects()].some(rect => rect.width &&
                        (rect.left < bounds.left - 1 || rect.right > bounds.right + 1 ||
                         rect.top < bounds.top - 1 || rect.bottom > bounds.bottom + 1));
                }).map(control => control.textContent.trim())
        """)
        assert not overflow, overflow

    async def assert_button_rows(self, selector):
        result = await self.js(f"""
            (() => {{
                const group = document.querySelector({json.dumps(selector)});
                const controlGap = Number.parseFloat(
                    getComputedStyle(document.documentElement).getPropertyValue('--control-gap')
                );
                const bounds = group.getBoundingClientRect();
                const lines = new Map();
                for (const control of group.querySelectorAll(':scope > button, :scope > a')) {{
                    if (!control.getClientRects().length) continue;
                    const box = control.getBoundingClientRect();
                    const top = Math.round(box.top);
                    if (!lines.has(top)) lines.set(top, []);
                    lines.get(top).push(box);
                }}
                const near = (a, b) => Math.abs(a - b) <= 1;
                return [...lines.values()].every((row, index, rows) =>
                    near(row[0].left, bounds.left) && near(row.at(-1).right, bounds.right)
                    && (index === 0 || near(row[0].top - rows[index - 1][0].bottom, controlGap))
                    && row.every((box, column) =>
                        box.height >= 44 && near(box.width, row[0].width)
                        && near(box.height, row[0].height)
                        && (column === 0 || near(box.left - row[column - 1].right, controlGap))
                    )
                ) && lines.size > 0;
            }})()
        """)
        assert result, selector

    async def tap(self, selector):
        point = await self.js(
            f"(()=>{{const r=document.querySelector({json.dumps(selector)})"
            ".getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
        )
        await self.call("Emulation.setTouchEmulationEnabled", {"enabled": True})
        await self.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
        await self.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    async def drag(self, source, target, touch=False):
        start = await self.js(
            f"(()=>{{const r=document.querySelector({json.dumps(source)})"
            ".getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
        )
        end = await self.js(
            f"(()=>{{const r=document.querySelector({json.dumps(target)})"
            ".getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+8}})()"
        )
        if touch:
            await self.call("Emulation.setTouchEmulationEnabled", {"enabled": True})
            await self.call(
                "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [start]}
            )
            await self.call("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [end]})
            await self.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            return
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **start})
        await self.call(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", **start, "button": "left", "buttons": 1, "clickCount": 1},
        )
        await self.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", **end, "button": "left", "buttons": 1},
        )
        await self.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", **end, "button": "left", "buttons": 0, "clickCount": 1},
        )


async def browser_editor_layout(browser):
    for width, height in ((320, 568), (375, 667), (390, 844), (430, 932), (844, 390), (1200, 800)):
        await browser.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 1000},
        )
        assert await browser.js("document.documentElement.scrollWidth <= innerWidth")
        assert await browser.js("""
            [...document.querySelectorAll('.catalog-entry button')].every(button => {
                const box = button.getBoundingClientRect();
                return box.width >= 44 && box.height >= 44;
            })
        """)
        assert await browser.js("document.getElementById('jump-catalog').offsetHeight > 0") == (
            width < 1000
        )
        if height < 500:
            assert await browser.js(
                "getComputedStyle(document.querySelector('footer')).position"
            ) == ("static")
        await browser.assert_button_text_fits()
        await browser.assert_button_rows(".editor-tools")
        await browser.assert_button_rows("#editor footer")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
    )
    await browser.js("document.getElementById('jump-catalog').click()")
    assert await browser.js("document.activeElement.id") == "catalog-heading"
    await browser.js("document.getElementById('jump-setlist').click()")
    assert await browser.js("document.activeElement.id") == "setlist-heading"


async def browser_initial_background(browser, origin):
    # Leave the first paint without the usual stylesheet, SDK, or app bundle.
    telegram_pattern = {"urlPattern": "https://telegram.org/*"}
    await browser.call(
        "Fetch.enable",
        {
            "patterns": [
                telegram_pattern,
                {"urlPattern": "*/style.css"},
                {"urlPattern": "*/app.js"},
            ],
        },
    )
    start = f"{origin}/miniapp/?workspace=1"
    await browser.call(
        "Emulation.setEmulatedMedia",
        {
            "features": [{"name": "prefers-color-scheme", "value": "dark"}],
        },
    )
    await browser.call("Page.navigate", {"url": start})
    await browser.wait("document.documentElement.dataset.theme === 'dark'")
    assert await browser.js("getComputedStyle(document.documentElement).backgroundColor") == (
        "rgb(28, 28, 30)"
    )

    await browser.call(
        "Emulation.setEmulatedMedia",
        {
            "features": [{"name": "prefers-color-scheme", "value": "light"}],
        },
    )
    fragment = urlencode({"tgWebAppThemeParams": json.dumps({"bg_color": "#232125"})})
    await browser.call("Page.navigate", {"url": f"{start}&step=launch#{fragment}"})
    await browser.wait("location.search.includes('step=launch')")
    await browser.wait("document.documentElement.dataset.theme === 'dark'")
    assert await browser.js("getComputedStyle(document.documentElement).backgroundColor") == (
        "rgb(35, 33, 37)"
    )

    await browser.js("""
        sessionStorage.setItem('setlistbot.start-theme', JSON.stringify({
            scheme: 'dark', background: '#232125',
        }));
    """)
    await browser.call("Page.navigate", {"url": f"{start}&step=internal"})
    await browser.wait("location.search.includes('step=internal')")
    await browser.wait("document.documentElement.dataset.theme === 'dark'")
    assert await browser.js("getComputedStyle(document.documentElement).backgroundColor") == (
        "rgb(35, 33, 37)"
    )
    await browser.js("sessionStorage.removeItem('setlistbot.start-theme')")
    await browser.call("Emulation.setEmulatedMedia", {"features": []})
    await browser.call("Fetch.enable", {"patterns": [telegram_pattern]})


async def emulate_keyboard(browser, *, height, offset):
    await browser.js(f"""
        Object.defineProperties(visualViewport, {{
          height: {{ value: {height}, configurable: true }},
          offsetTop: {{ value: {offset}, configurable: true }},
        }});
        visualViewport.dispatchEvent(new Event('resize'));
    """)


async def dismiss_emulated_keyboard(browser):
    await browser.js("""
        document.activeElement.blur();
        delete visualViewport.height;
        delete visualViewport.offsetTop;
        visualViewport.dispatchEvent(new Event('resize'));
    """)
    await browser.wait("!document.querySelector('.keyboard-focus')")


async def browser_modal_scroll(browser):
    previous_scroll = await browser.js("window.scrollY")
    await browser.js("""
        (() => {
            const reserve = document.createElement('div');
            reserve.id = 'modal-test-background';
            reserve.style.height = '1200px';
            document.body.append(reserve);
            window.scrollTo(0, 210);
        })()
    """)
    await browser.wait("window.scrollY === 210")
    await browser.js("""
        (() => {
            const modal = document.createElement('dialog');
            modal.id = 'modal-scroll-test';
            const close = document.createElement('button');
            close.textContent = 'Закрыть';
            const content = document.createElement('div');
            content.style.height = '1200px';
            modal.append(close, content);
            document.body.append(modal);
            modal.showModal();
        })()
    """)
    point = await browser.js("""
        (() => {
            const box = document.getElementById('modal-scroll-test').getBoundingClientRect();
            return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
        })()
    """)
    wheel = {"type": "mouseWheel", **point, "deltaX": 0, "deltaY": 240}
    await browser.call("Input.dispatchMouseEvent", wheel)
    await browser.wait("document.getElementById('modal-scroll-test').scrollTop > 0")
    assert await browser.js("""
        (() => {
            const modal = document.getElementById('modal-scroll-test');
            const box = modal.getBoundingClientRect();
            const x = box.left + box.width / 2, y = box.top + box.height / 2;
            function swipe(fromY, delta) {
                const steps = [['touchstart', fromY], ['touchmove', fromY + delta]];
                for (const [type, clientY] of steps) {
                    const touch = new Touch({ identifier: 1, target: modal, clientX: x, clientY });
                    const options = { touches: [touch], bubbles: true, cancelable: true };
                    const event = new TouchEvent(type, options);
                    if (!modal.dispatchEvent(event)) return true;
                }
                return false;
            }
            const middleCanScroll = !swipe(y, -20);
            modal.scrollTop = 0;
            const topLocked = swipe(y, 20);
            modal.scrollTop = modal.scrollHeight;
            const bottomLocked = swipe(y, -20);
            return middleCanScroll && topLocked && bottomLocked && swipe(box.top - 2, 20);
        })()
    """)
    await browser.call("Input.dispatchMouseEvent", wheel)
    await browser.call("Input.dispatchMouseEvent", {**wheel, "x": 195, "y": 2})
    await browser.js(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    assert await browser.js("window.scrollY === 210")
    await browser.js("""
        (() => {
            const modal = document.getElementById('modal-scroll-test');
            modal.close();
            modal.remove();
        })()
    """)
    assert await browser.js("window.scrollY === 210")
    await browser.call("Input.dispatchMouseEvent", wheel)
    await browser.wait("window.scrollY > 210")
    await browser.js(f"""
        document.getElementById('modal-test-background').remove();
        window.scrollTo(0, {previous_scroll});
    """)


async def browser_music_search(browser, engine, band):
    from test_repertoire_workspace_music import ready_worker

    await browser.click_named("a", "К списку песен")
    await browser.click_named("a", "🌐 Найти песни в интернете")
    await browser.wait("!!document.querySelector('#music-query')")
    await browser.js("""
        const input = document.querySelector('#music-query');
        input.value = 'Example artist';
        input.form.requestSubmit();
    """)
    await process_catalog_request(engine)
    await ready_worker(engine, band)
    await browser.call("Page.reload")
    await browser.click_named("button", "Example artist")
    await process_catalog_request(engine)
    await ready_worker(engine, band)
    await browser.call("Page.reload")
    await browser.wait("!!document.querySelector('input[id^=track-time]')")
    await browser.js("""
        const input = document.querySelector('input[id^=track-time]');
        input.value = '03:30';
        input.form.requestSubmit();
    """)
    await process_catalog_request(engine)
    await browser.wait("!document.querySelector('input[id^=track-time]')")
    await browser.click_named("button", "✅ Продолжить")
    await process_catalog_request(engine)
    await browser.wait("document.querySelector('#workspace-content').innerText.includes('03:30')")
    await browser.click_named("button", "✅ Добавить песни · 1")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content').innerText.includes('Добавлено песен: 1.')"
    )


@pytest.mark.skipif(
    shutil.which("chromium") is None, reason="Chromium is optional for local UI check"
)
@pytest.mark.parametrize("launch_mode", ["session", "public", "workspace"])
async def test_phone_editor_drag_history_pause_save_and_live_themes(
    engine, world, tmp_path, launch_mode, monkeypatch
):
    from datetime import date

    public_launch = launch_mode == "public"
    if launch_mode == "workspace":
        monkeypatch.setattr("setlist_bot.application.workspace.today", lambda: date(2026, 12, 31))
    async with PostgresUnitOfWork(engine) as uow:
        from setlist_bot.application.catalog import Catalog

        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Меланхолия", "duration_seconds": 246}
        )
        lists = Setlists(uow.store, world.admin)
        if launch_mode == "workspace":
            from conftest import invite_fixture_member

            from setlist_bot.transport.publications import queue_publication

            await lists.edit_songs(world.group.id, world.concert.id, 0, ["Комета"])
            await invite_fixture_member(uow.store, world.concert, world.member)
            member_lists = Setlists(uow.store, world.member)
            await member_lists.edit_songs(
                world.group.id, world.concert.id, 0, ["Меланхолия"], replace=True, private=True
            )
            await queue_publication(uow.store, world.member, world.group.id, world.concert.id, 1)
        past = await lists.create(world.group.id, date(2025, 12, 1), "Прошлое выступление")
        _, version, _ = await lists.read(world.group.id, past.id)
        await lists.edit_songs(world.group.id, past.id, version, ["Комета"])
        _, version, _ = await lists.read(world.group.id, past.id)
        await lists.approve(world.group.id, past.id, version)
    if launch_mode == "workspace":
        from aiogram import Bot
        from test_telegram import FakeTelegram

        from setlist_bot.main import deliver_one_outbox

        async with Bot("123456:TEST", session=FakeTelegram()) as bot:
            assert await deliver_one_outbox(bot, engine)
    identifier, _ = await session(engine, world)
    from test_miniapp_group import template

    public_template = await template(engine, world) if public_launch else None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        http_port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{http_port}"
    # Test-only HTTP origin replacement, not a production authentication bypass.
    app = create_app(settings(engine).model_copy(update={"public_base_url": origin}), engine)
    server = TestServer(app, port=http_port)
    await server.start_server()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            shutil.which("chromium"),
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    browser = None
    try:
        async with aiohttp.ClientSession() as client:
            ws = await client.ws_connect(await browser_tab(client, port, process))
            browser = Browser(ws)
            await browser.call("Page.enable")
            await browser.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 900,
                    "deviceScaleFactor": 1,
                    "mobile": True,
                },
            )
            await browser.call(
                "Fetch.enable", {"patterns": [{"urlPattern": "https://telegram.org/*"}]}
            )
            stub = """
                window.Telegram={WebApp:{initData:INIT_DATA,colorScheme:'light',themeParams:{},
                BackButton:{show:()=>{},onClick:fn=>{window.nativeBack=fn}},
                onEvent:(name,fn)=>{if(name==='themeChanged') window.themeChange=fn},
                ready:()=>{},expand:()=>{},
                close:()=>{window.testClosed=true},
                isVersionAtLeast:()=>true,setBackgroundColor:color=>{window.nativeBackground=color},
                enableClosingConfirmation:()=>{},disableClosingConfirmation:()=>{}}};
            """.replace(
                "INIT_DATA",
                json.dumps(
                    signed(chat_type="supergroup", start_param="sl_" + public_template.hex)
                    if public_launch
                    else signed()
                ),
            )
            await browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": stub})
            if launch_mode == "session":
                await browser_initial_background(browser, origin)
            if launch_mode == "workspace":
                await browser.call(
                    "Fetch.enable",
                    {
                        "patterns": [
                            {"urlPattern": "https://telegram.org/*"},
                            {"urlPattern": "*/unbounded.ttf"},
                        ]
                    },
                )
            await browser.call(
                "Page.navigate",
                {
                    "url": origin
                    + "/miniapp/"
                    + ("#session=" + str(identifier) if launch_mode == "session" else ""),
                },
            )
            if launch_mode == "workspace":
                await browser.wait("document.querySelectorAll('.workspace-list a').length === 2")
                assert await browser.js("""
                    const placeholder = document.querySelector('.band-name');
                    document.querySelector('.band-list').dataset.nameFont === 'loading'
                    && getComputedStyle(placeholder).visibility === 'hidden'
                    && document.querySelector('.band-list a').getBoundingClientRect().height >= 68
                    && !!document.querySelector('#workspace-content nav a')
                """)
                await browser.wait(
                    "document.querySelector('.band-list').dataset.nameFont === 'fallback'"
                )
                assert browser.font_request
                async with client.get(f"{origin}/miniapp/unbounded.ttf") as response:
                    assert response.status == 200 and response.content_type == "font/ttf"
                    font = await response.read()
                await browser.call(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": browser.font_request,
                        "responseCode": 200,
                        "responseHeaders": [{"name": "Content-Type", "value": "font/ttf"}],
                        "body": base64.b64encode(font).decode(),
                    },
                )
                await browser.wait("[...document.fonts].some(font => font.status === 'loaded')")
                assert await browser.js("""
                    const name = document.querySelector('.band-name');
                    getComputedStyle(name).visibility === 'visible'
                      && !getComputedStyle(name).fontFamily.includes('Unbounded')
                """)
                await browser.call(
                    "Fetch.enable", {"patterns": [{"urlPattern": "https://telegram.org/*"}]}
                )
                await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1"})
                await browser.wait(
                    "document.querySelector('.band-list')?.dataset.nameFont === 'ready'"
                )
                assert await browser.js("""
                    (async () => {
                        const link = document.querySelector('.band-list a');
                        const name = link.querySelector('.band-name');
                        const original = name.textContent;
                        name.textContent = 'Очень длинное название коллектива Midnight Avenue';
                        await document.fonts.ready;
                        const style = getComputedStyle(link);
                        const fits = style.fontFamily.includes('Unbounded')
                          && style.textDecorationLine === 'none'
                          && link.clientHeight > 68 && link.scrollWidth <= link.clientWidth;
                        name.textContent = original;
                        return fits;
                    })()
                """)
                await browser.wait(
                    "document.querySelector('a[href*=\"view=inbox\"]')"
                    "?.textContent.includes(' · 1')"
                )
                await browser.js("document.querySelector('a[href*=\"view=inbox\"]').click()")
                await browser.click_named("#workspace-content a", "Предложение сет-листа")
                await browser.click_named(".shared-tools button", "Показать вариант")
                await browser.wait("!!document.querySelector('dialog[open] .shared-setlist')")
                await browser.js("window.nativeBack()")
                await browser.click_named("#workspace-content a", "К входящим запросам")
                await browser.click_named("#workspace-content a", "Мои коллективы")
                await browser.wait("document.querySelectorAll('.workspace-list a').length === 2")
                await browser.tap(".workspace-list a")
                await browser.wait(
                    "document.querySelectorAll('.workspace-list button').length === 2"
                )
                await browser.click_named(".workspace-list a", "Новый год")
                await browser.wait(
                    "document.querySelector('.shared-setlist')?.innerText.includes('Комета')"
                )
                await browser.wait("!!document.getElementById('readiness-title')")
                await browser.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 320,
                        "height": 640,
                        "deviceScaleFactor": 1,
                        "mobile": True,
                    },
                )
                assert await browser.js("""
                    (() => {
                        const nav = document.querySelector('#workspace-content > nav');
                        const details = document.querySelector('.performance-details-nav');
                        const [data, roster, timing, remove] = [...details.children];
                        const [a, b, c, d] = [...details.children].map(button =>
                          button.getBoundingClientRect());
                        return nav.childElementCount === 1
                          && Math.abs(nav.firstElementChild.offsetWidth - nav.clientWidth) <= 1
                          && data.textContent === 'Данные выступления'
                          && roster.textContent.includes('Состав выступления')
                          && timing.textContent === 'Тайминг выступления'
                          && remove.textContent === 'Удалить выступление'
                          && remove.classList.contains('destructive')
                          && a.top === b.top && c.top === d.top && c.top > a.top
                          && a.left === c.left && b.left === d.left && a.left < b.left
                          && Math.abs(a.width - b.width) <= 1 && a.height === c.height
                          && details.nextElementSibling.classList.contains('performance-readiness');
                    })()
                """)
                await browser.assert_button_text_fits()
                await browser.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 390,
                        "height": 900,
                        "deviceScaleFactor": 1,
                        "mobile": True,
                    },
                )
                assert await browser.js("""
                    !!document.querySelector('.performance-readiness .status-ready')
                    && !!document.querySelector('.performance-readiness .status-pending')
                """)
                await browser.click_named(".performance-details-nav a", "Тайминг выступления")
                await browser.wait("!!document.querySelector('.timing-edit-table')")
                await browser.click_named(".timing-fields button", "Добавить строку")
                await browser.js("document.activeElement.click()")
                await browser.wait("!!document.querySelector('.timing-wheel-dialog[open]')")
                assert await browser.js("""
                    [...document.querySelectorAll('.timing-wheels > :last-child button')]
                      .map(button => button.textContent).join(',')
                      === '00,05,10,15,20,25,30,35,40,45,50,55'
                """)
                await browser.click_named(".timing-wheel-dialog button", "55")
                await browser.click_named(".timing-wheel-dialog button", "Готово")
                assert await browser.js("document.querySelector('[name=start]').value === '12:55'")
                await browser.js("document.querySelector('.timing-form [name=label]').focus()")
                await emulate_keyboard(browser, height=360, offset=62)
                await browser.wait("""
                    Math.abs(document.activeElement.getBoundingClientRect().top
                      - visualViewport.offsetTop - 12) < 2
                """)
                await dismiss_emulated_keyboard(browser)
                await browser.js("""
                    const field = document.querySelector('.timing-form [name=label]');
                    field.value = 'Сбор'; field.focus();
                    const enter = new KeyboardEvent('keydown', {key: 'Enter', bubbles: true});
                    field.dispatchEvent(enter);
                """)
                assert await browser.js(
                    "document.querySelectorAll('.timing-edit-table tbody tr').length === 2"
                )
                await browser.click_named("#workspace-content nav a", "Назад")
                await browser.wait("!!document.getElementById('readiness-title')")
                await browser.click_named(".performance-details-nav a", "Тайминг выступления")
                await browser.wait("!!document.querySelector('.timing-edit-table')")
                assert await browser.js("!document.querySelector('.timing-edit-table tbody tr')")
                await browser.click_named("#workspace-content nav a", "Назад")
                await browser.wait("!!document.getElementById('readiness-title')")
                await browser.assert_button_rows(".performance-details-nav")
                await browser.assert_button_rows("#workspace-content > .toolbar")
                assert await browser.js("""
                    const editor = document.querySelector('#workspace-content button.primary');
                    !!(editor.compareDocumentPosition(document.querySelector('.shared-setlist'))
                       & Node.DOCUMENT_POSITION_FOLLOWING)
                """)
                # An older response must not populate a newly opened dialog.
                await browser.js("""
                    const fetchNormally = window.fetch;
                    window.fetch = async (...args) => {
                        const response = await fetchNormally(...args);
                        if (new URL(args[0], location.href).pathname.endsWith('/shared_history'))
                            await new Promise(resolve => { window.releaseHistory = resolve; });
                        return response;
                    };
                """)
                await browser.click_named("#workspace-content button", "История")
                await browser.wait("typeof window.releaseHistory === 'function'")
                await browser.js("window.nativeBack()")
                await browser.click_named("#workspace-content button", "Входящие запросы")
                await browser.js("window.releaseHistory(); window.fetch = fetchNormally")
                await browser.click_named(".shared-tools button", "Показать вариант")
                await browser.wait(
                    "document.querySelector('dialog[open] .shared-setlist')?.innerText"
                    ".includes('Меланхолия')"
                )
                await browser.click_named(".shared-tools button", "Отклонить")
                await process_catalog_request(engine)
                await browser.wait(
                    "document.getElementById('workspace-status')?.textContent"
                    " === 'Предложение отклонено.'"
                )
                await browser.click_named("#workspace-content button", "Входящие запросы")
                await browser.wait(
                    "document.querySelector('dialog[open]')?.innerText"
                    ".includes('Нет запросов на рассмотрении.')"
                )
                await browser.js("window.nativeBack()")
                await browser.wait("!document.querySelector('dialog[open]')")
                assert await browser.js("new URLSearchParams(location.search).get('view')") == (
                    "performance"
                )
                assert not await browser.js(
                    "[...document.querySelectorAll('#workspace-content button')].some("
                    "button => button.textContent === 'Редактировать общий сет-лист')"
                )
                await browser.click_named("#workspace-content button", "🎵 Открыть редактор")
                await browser.wait("!document.getElementById('save-shared').hidden")
                await browser.js("""
                    document.getElementById('add-pause').click();
                    document.getElementById('pause-value').value = '1';
                    document.getElementById('pause-text').value = 'Пауза для настройки';
                    document.getElementById('pause-form').requestSubmit();
                """)
                await browser.wait(
                    "document.querySelector('#setlist .pause-label')?.textContent"
                    ".includes('Пауза для настройки')"
                )
                await browser.click_named("#editor button", "Сохранить сет-лист как общий")
                await process_catalog_request(engine)
                await browser.wait(
                    "document.getElementById('status').textContent === 'Общий сет-лист сохранён.'"
                )
                await browser.js("document.getElementById('back-bot').click()")
                await browser.wait(
                    "document.querySelector('.shared-setlist')?.innerText"
                    ".includes('Пауза для настройки')"
                )
                await browser.click_named("#workspace-content button", "✅ Утвердить")
                await process_catalog_request(engine)
                await browser.wait(
                    "document.querySelector('#workspace-content').innerText.includes('Утверждён')"
                )
                await browser.call(
                    "Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&band={world.group.id}"}
                )
                await browser.wait("!!document.querySelector('a.approved-setlist.status-ready')")
                await browser.click_named("a.approved-setlist", "✓Сет-лист утверждён")
                await browser.wait("!!document.querySelector('#shared-list')")
                await browser.click_named("#workspace-content button", "📄 Скачать PDF")
                downloads = tmp_path / "downloads"
                await browser.call(
                    "Browser.setDownloadBehavior",
                    {"behavior": "allow", "downloadPath": str(downloads)},
                )
                await browser.click_named(".shared-tools button", "Скачать PDF")
                await process_catalog_request(engine)
                await browser.wait("!!document.querySelector('.workspace-pdf-downloads a')")
                filename = await browser.js(
                    "document.querySelector('.workspace-pdf-downloads a').download"
                )
                for _ in range(100):
                    if (downloads / filename).is_file() and (downloads / filename).stat().st_size:
                        break
                    await asyncio.sleep(0.05)
                assert (downloads / filename).read_bytes().startswith(b"%PDF-")
                (downloads / filename).unlink()
                await browser.js("""
                    document.getElementById('workspace-pdf-audience').value = 'all';
                """)
                await browser.click_named(".shared-tools button", "Скачать PDF")
                await process_catalog_request(engine)
                await browser.wait(
                    "document.querySelectorAll('.workspace-pdf-downloads a[download]').length === 2"
                )
                for index in range(2):
                    filename = await browser.js(f"""
                        (() => {{
                            const links = document.querySelectorAll('.workspace-pdf-downloads a');
                            const link = links[{index}];
                            link.click();
                            return link.download;
                        }})()
                    """)
                    for _ in range(100):
                        if (downloads / filename).is_file() and (
                            downloads / filename
                        ).stat().st_size:
                            break
                        await asyncio.sleep(0.05)
                    assert (downloads / filename).read_bytes().startswith(b"%PDF-")
                await browser.click_named(".shared-tools button", "Закрыть")
                await browser.click_named("#workspace-content button", "Вернуть в работу")
                await process_catalog_request(engine)
                await browser.wait(
                    "document.querySelector('#workspace-content').innerText.includes('В работе')"
                )
                await browser.click_named("#workspace-content button", "История")
                await browser.wait(
                    "document.querySelector('.shared-tools').innerText"
                    ".includes('Возвращён в работу')"
                )
                await browser.click_named(".shared-tools button", "Закрыть")
                await browser.click_named("#workspace-content button", "🎵 Открыть редактор")
                await browser.wait("document.querySelectorAll('.catalog-entry').length === 2")
                assert "Новый год" in await browser.js(
                    "document.getElementById('event').textContent"
                )
                await browser.js("document.getElementById('back-bot').click()")
                await browser.wait("!!document.querySelector('.shared-setlist')")
                await browser.click_named("a", "◀️ К выступлениям")
                await browser.wait(
                    "document.querySelectorAll('.workspace-list button').length === 2"
                )
                assert await browser.js("document.getElementById('editor').hidden")
                await browser.js("document.querySelector('.workspace-list button').click()")
                await browser.wait("document.querySelectorAll('.catalog-entry').length === 2")
                await browser.js("window.nativeBack()")
                await browser.wait(
                    "document.querySelectorAll('.workspace-list button').length === 2"
                )
                await browser.js("document.querySelector('a[href*=repertoire]').click()")
                await browser.wait("document.querySelectorAll('.workspace-list a').length === 2")
                await browser.js("""
                    document.getElementById('repertoire-query').value = 'Kometa';
                    document.querySelector('.workspace-search').requestSubmit();
                """)
                await browser.wait("document.querySelectorAll('.workspace-list a').length === 1")
                await browser.js("document.querySelector('.workspace-list a').click()")
                await browser.wait(
                    "document.querySelector('.song-details')?.innerText.includes('110')"
                )
                await browser.js(
                    "Array.from(document.querySelectorAll('#workspace-content button'))"
                    ".find(b => b.textContent === 'Копировать').click()"
                )
                await browser.wait("document.getElementById('song-dialog').open")
                assert await browser.js("document.getElementById('song-title').value") == ""
                assert await browser.js("document.getElementById('song-duration').value") == "03:38"
                await browser.js("""
                    document.getElementById('song-title').value = 'Комета акустика';
                    document.getElementById('song-form').requestSubmit();
                """)
                await process_catalog_request(engine)
                await browser.wait(
                    "document.getElementById('workspace-status').textContent"
                    " === 'Репертуар обновлён.'"
                )
                assert not await browser.js("document.getElementById('song-dialog').open")
                await browser.js(
                    "Array.from(document.links)"
                    ".find(a => a.textContent === 'К списку песен').click()"
                )
                await browser.wait(
                    "document.getElementById('repertoire-query')?.value === 'Kometa'"
                )
                await browser.js("""
                    Array.from(document.links).find(a => a.textContent === 'Импорт таблицы').click()
                """)
                await browser.wait("!!document.getElementById('import-source')")
                await browser.js(r"""
                    const source = 'TRACKS\tTIME\tKEY\nИнтро\t00:05\tAm';
                    document.getElementById('import-source').value = source;
                    document.querySelector('.import-fields').form.requestSubmit();
                """)
                await browser.wait(
                    "document.querySelector('#workspace-content button.primary')?.hidden === false"
                )
                assert await browser.js(
                    "document.querySelector('details').textContent.includes('KEY: Am')"
                )
                await browser.js(
                    "document.querySelector('#workspace-content button.primary').click()"
                )
                await process_catalog_request(engine)
                await browser.wait(
                    "document.querySelector('#workspace-content').innerText"
                    ".includes('Добавлено песен: 1.')"
                )
                await browser_music_search(browser, engine, world.group.id)
                await browser_manage_performance(browser, engine, world.group.id, origin)
                await browser_staff_directory(browser, engine, world.group.id, origin)
                await browser_roster(browser, engine, world, origin)
                await browser_guest_gig(browser, engine, world, origin)
                await browser_approve_self_substitution(browser, engine, world, origin)
                await browser_schedule(browser, engine, origin)
                await browser_profile(browser, engine, origin)
                await browser_band_settings(browser, engine, world.group.id, origin)
                await browser_help(browser, engine, world.admin, origin)
                await browser_band_trash(browser, engine, world.other.id, origin)
                await browser_team(browser, engine, world.group.id, origin)
                return
            await browser.wait("document.querySelectorAll('.catalog-entry').length===2")
            assert await browser.js(
                "Array.from(document.querySelectorAll('#average-pause')).length===1 && "
                "document.getElementById('undo').nextElementSibling.id==='average-pause' && "
                "document.getElementById('average-pause').nextElementSibling.id==='add-pause'"
            )
            assert (
                await browser.js("document.getElementById('average-pause').textContent")
                == "Сред. пауза"
            )
            assert await browser.js("document.getElementById('older').textContent") == "История"
            assert await browser.js("document.getElementById('newer').hidden")
            assert await browser.js("document.getElementById('save').hidden")
            assert await browser.js(
                "document.getElementById('undo').ariaLabel === 'Отменить последнее действие'"
            )
            assert not await browser.js("!!document.querySelector('#remove-zone, [data-remove]')")
            assert (
                await browser.js("document.getElementById('add-pause-bottom').textContent")
                == "⏸ Пауза"
            )
            assert await browser.js(
                "(()=>{const s=getComputedStyle(document.getElementById('add-pause-bottom'));"
                "return s.borderTopStyle==='dashed' && s.backgroundColor==='rgba(0, 0, 0, 0)'})()"
            )
            if launch_mode == "session":
                await browser_editor_layout(browser)
                await browser_modal_scroll(browser)
            assert await browser.js("document.getElementById('catalog-pages').hidden")
            await browser.js("""
                const search = document.getElementById('search');
                search.value = 'no such song';
                search.dispatchEvent(new Event('input'));
            """)
            assert await browser.js("document.getElementById('catalog-empty').innerText") == (
                "Песни не найдены."
            )
            # A real desktop mouse sequence must keep focus despite scrolling on pointerdown.
            await browser.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1200,
                    "height": 800,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await browser.js("document.getElementById('search').scrollIntoView({block:'center'})")
            point = await browser.js(
                "(()=>{const r=document.getElementById('search').getBoundingClientRect();"
                "return {x:r.x+20,y:r.y+20}})()"
            )
            for event_type in ("mousePressed", "mouseReleased"):
                await browser.call(
                    "Input.dispatchMouseEvent",
                    {"type": event_type, **point, "button": "left", "clickCount": 1},
                )
            await browser.wait("document.activeElement.id==='search'")
            await browser.call("Input.insertText", {"text": "Ком"})
            assert await browser.js("document.getElementById('search').value") == "Ком"
            await browser.js("document.getElementById('search').blur()")
            await browser.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 900,
                    "deviceScaleFactor": 1,
                    "mobile": True,
                },
            )
            # RU/EN: Native touch must still add a song while closing the search keyboard.
            await browser.js("document.getElementById('search').focus()")
            await browser.call("Input.insertText", {"text": "Ком"})
            await browser.wait("document.querySelectorAll('.catalog-entry').length===1")
            search_at_top = (
                "Math.abs(document.getElementById('search').getBoundingClientRect().top"
                "-(visualViewport?.offsetTop ?? 0)-12)<2"
            )
            await browser.wait(search_at_top)
            await browser.js("document.getElementById('search').click()")
            assert await browser.js("document.getElementById('search').value") == "Ком"
            await browser.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 600,
                    "deviceScaleFactor": 1,
                    "mobile": True,
                },
            )
            await browser.wait(search_at_top)
            await browser.js("window.originalAdd = document.querySelector('.catalog-entry .add')")
            await browser.tap(".catalog-entry .add")
            await browser.wait("document.querySelectorAll('#setlist li').length===1")
            assert await browser.js(
                "window.originalAdd === document.querySelector('.catalog-entry .add')"
            )
            assert await browser.js("document.activeElement.id!=='search'")
            assert await browser.js("document.getElementById('search').value") == "Ком"
            await browser.js("document.getElementById('search').focus()")
            assert await browser.js("document.getElementById('search').value") == ""
            await browser.call("Input.insertText", {"text": "Мел"})
            await browser.wait(
                "document.getElementById('status').textContent.includes('Сохранено')"
            )
            assert await browser.js("document.getElementById('search').value") == "Мел"
            await browser.call(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            )
            await browser.call(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            )
            assert await browser.js("document.activeElement.id!=='search'")
            await browser.js("document.getElementById('search').focus()")
            assert await browser.js("document.getElementById('search').value") == ""
            await browser.call("Input.insertText", {"text": "Ком"})
            await browser.js("document.querySelector('.add').click()")
            assert await browser.js("document.activeElement.id!=='search'")
            await browser.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 900,
                    "deviceScaleFactor": 1,
                    "mobile": True,
                },
            )
            await browser.js("window.scrollTo(0,0)")
            # No manual Save: exercise the debounce and real HTTP autosave.
            await browser.wait(
                "document.getElementById('status').textContent.includes('Сохранено')"
            )
            await browser.js(
                "document.getElementById('add-pause-bottom').click(); "
                "document.getElementById('pause-value').value='3'; "
                "document.getElementById('pause-form').requestSubmit()"
            )
            await browser.wait("document.querySelectorAll('#setlist li').length===3")
            assert await browser.js("document.getElementById('total').textContent") == "10:21"
            transparent_pause = """(()=>{
                const style=getComputedStyle(document.querySelector('#setlist .pause'));
                return style.backgroundColor==='rgba(0, 0, 0, 0)'
                  && style.borderTopStyle==='dashed';
            })()"""
            assert await browser.js(transparent_pause)
            # RU/EN: An autosave acknowledgement during editing must not change the target row.
            await browser.js(
                "document.querySelector('#setlist .pause-label').click();"
                "document.getElementById('pause-text').value='Крабы уносят микрофонную стойку';"
                "document.getElementById('pause-form').requestSubmit()"
            )
            await browser.wait(
                "document.querySelector('#setlist .pause .song-name').textContent.includes('Крабы')"
            )
            await browser.js("document.querySelector('#setlist .pause-label').click()")
            await browser.wait(
                "document.getElementById('status').textContent.includes('Сохранено')"
            )
            await browser.js(
                "document.getElementById('pause-text').value='';"
                "document.getElementById('pause-form').requestSubmit()"
            )
            await browser.wait(
                "document.querySelector('#setlist .pause .song-name').textContent===''"
            )
            assert await browser.js("document.getElementById('total').textContent") == "10:21"
            await browser.js(
                "document.querySelector('#setlist .pause-label').click();"
                "document.getElementById('pause-text').value='Не сохранять';"
                "document.getElementById('pause-cancel').click()"
            )
            assert (
                await browser.js("document.querySelector('#setlist .pause .song-name').textContent")
                == ""
            )
            # A late duration reply must not apply an already cancelled pause.
            await browser.js("""(()=>{
                window.originalFetch=window.fetch;
                window.fetch=(...args)=>String(args[0]).includes('/api/pause')
                  ? new Promise(resolve=>{window.resumePause=()=>resolve(new Response(
                    JSON.stringify({seconds:999}),{status:200}));})
                  : window.originalFetch(...args);
                document.getElementById('add-pause-bottom').click();
                document.getElementById('pause-value').value='3';
                document.getElementById('pause-form').requestSubmit();
            })()""")
            await browser.wait("typeof window.resumePause==='function'")
            await browser.js("""(async()=>{
                document.getElementById('pause-cancel').click();
                window.resumePause(); await new Promise(requestAnimationFrame);
                window.fetch=window.originalFetch;
            })()""")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 3
            await browser.js(
                "document.getElementById('average-pause').click(); "
                "document.getElementById('average-value').value='30'; "
                "document.getElementById('average-form').requestSubmit()"
            )
            assert await browser.js("document.getElementById('total').textContent") == "10:46"
            await browser.wait(
                "document.getElementById('status').textContent.includes('Сохранено')"
            )
            await browser.js("document.getElementById('undo').click()")
            assert await browser.js("document.getElementById('total').textContent") == "10:21"
            await browser.drag(
                "#setlist li:last-child .handle", "#setlist li:first-child", touch=True
            )
            assert await browser.js(
                "document.querySelector('#setlist li').classList.contains('pause')"
            )
            # Theme changes must not lose the in-progress list.
            matching_timing_style = """(() => {
                const total = getComputedStyle(document.querySelector('#total'));
                const delta = getComputedStyle(document.querySelector('#planned-delta'));
                const edit = getComputedStyle(document.querySelector('#planned-edit'));
                const values = document.querySelectorAll('#planned-edit, .timing > [aria-hidden]');
                return Array.from(values).every((element) => {
                    const style = getComputedStyle(element);
                    return ['fontFamily', 'fontSize', 'fontWeight', 'fontVariantNumeric', 'color']
                        .every((property) => style[property] === total[property]);
                }) && delta.color === total.color
                   && getComputedStyle(document.body).color === total.color
                   && edit.borderTopStyle === 'solid';
            })()"""
            await browser.js("window.Telegram.WebApp.colorScheme='dark'; window.themeChange()")
            assert await browser.js("document.documentElement.dataset.theme") == "dark"
            assert await browser.js("window.nativeBackground") == "#1c1c1e"
            assert (
                await browser.js(
                    "JSON.parse(sessionStorage.getItem('setlistbot.start-theme')).background"
                )
                == "#1c1c1e"
            )
            assert await browser.js(transparent_pause)
            assert await browser.js(matching_timing_style)
            await browser.js("window.scrollTo(0,0)")
            await browser.js("new Promise(resolve => setTimeout(resolve, 180))")
            screenshot = await browser.call("Page.captureScreenshot", {"format": "png"})
            (tmp_path / "miniapp-dark.png").write_bytes(base64.b64decode(screenshot["data"]))
            await browser.js("document.getElementById('older').click()")
            await browser.wait("document.querySelectorAll('#history-sources button').length > 0")
            await browser.js("document.querySelector('#history-sources button').click()")
            await browser.wait("!document.getElementById('use-history').hidden")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 1
            await browser.js("document.getElementById('newer').click()")
            await browser.wait("document.getElementById('use-history').hidden")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 3
            await browser.js("document.querySelector('#setlist li:first-child .remove').click()")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 2
            await browser.js("document.getElementById('undo').click()")
            await browser.wait(
                "document.getElementById('save').hidden && "
                "document.getElementById('save-status').textContent === 'Сохранено' && "
                "!document.getElementById('publish').disabled"
            )
            await browser.js("window.Telegram.WebApp.colorScheme='light'; window.themeChange()")
            assert await browser.js("document.documentElement.dataset.theme") == "light"
            assert await browser.js(matching_timing_style)
            await browser.js("new Promise(resolve => setTimeout(resolve, 180))")
            screenshot = await browser.call("Page.captureScreenshot", {"format": "png"})
            (tmp_path / "miniapp-light.png").write_bytes(base64.b64decode(screenshot["data"]))
            assert await browser.js("document.documentElement.scrollWidth<=innerWidth")
            async with PostgresUnitOfWork(engine) as uow:
                saved, _, _ = await Setlists(uow.store, world.admin).read(
                    world.group.id, world.concert.id, private=True
                )
                assert len(saved["blocks"][0]["items"]) == 3
                assert saved["blocks"][0]["items"][0]["pause_label"] == ""
                assert not (
                    await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
                )[0]["blocks"][0]["items"]
            # Explicit publication must wait for the existing Telegram worker acknowledgement.
            from aiogram import Bot
            from test_telegram import FakeTelegram

            from setlist_bot.domain.entities import Outbox
            from setlist_bot.main import deliver_one_outbox

            async def catalog_acknowledgement():
                for _ in range(100):
                    async with PostgresUnitOfWork(engine) as uow:
                        pending_catalog = await uow.store.find(
                            Outbox, support_kind="catalog", sent=False
                        )
                    if pending_catalog:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Catalog operation was not queued")
                async with Bot("123456:TEST", session=FakeTelegram()) as worker:
                    assert await deliver_one_outbox(worker, engine)
                await browser.wait("!document.getElementById('song-dialog').open")

            # RU/EN: Creating repertoire entries never inserts them into the current draft.
            await browser.js("document.getElementById('add-song').click()")
            await browser.wait("document.getElementById('song-dialog').open")
            if launch_mode == "session":
                await browser.js("document.getElementById('song-comment').focus()")
                await emulate_keyboard(browser, height=365, offset=68)
                await browser.wait("""
                    (() => {
                        const box = document.querySelector('#song-comment').getBoundingClientRect();
                        const sheet = document.querySelector('#song-dialog');
                        const top = visualViewport.offsetTop;
                        return document.activeElement.id === 'song-comment'
                          && sheet.getBoundingClientRect().top >= top
                          && box.top - top >= 12 && box.top - top < 105
                          && box.bottom < top + visualViewport.height;
                    })()
                """)
                await emulate_keyboard(browser, height=300, offset=90)
                await browser.wait("""
                    Math.abs(document.getElementById('song-dialog').getBoundingClientRect().top
                      - visualViewport.offsetTop - 12) < 2
                """)
                await dismiss_emulated_keyboard(browser)
            await browser.js("""
                document.getElementById('song-title').value='Звёздный ветер';
                document.getElementById('song-duration').value='02:30';
                document.getElementById('song-bpm').value='140';
                document.getElementById('song-lights').value='белый';
                document.getElementById('song-comment').value='Вступление';
                document.getElementById('song-aliases').value='Ветер';
                document.getElementById('song-form').requestSubmit();
            """)
            await catalog_acknowledgement()
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 3
            # Every occurrence is refreshed; order and blank pause are preserved.
            assert await browser.js("""['#setlist .song-edit', '#setlist .remove'].every(
                selector => {
                    const box = document.querySelector(selector).getBoundingClientRect();
                    return box.width >= 44 && box.height >= 44;
                }
            )""")
            await browser.js("document.querySelector('#setlist .song-edit').click()")
            await browser.wait("document.getElementById('song-dialog').open")
            await browser.js("""
                document.getElementById('song-title').value='Комета обновлённая';
                document.getElementById('song-duration').value='04:00';
                document.getElementById('song-bpm').value='150';
                document.getElementById('song-aliases').value='Ком';
                document.getElementById('song-form').requestSubmit();
            """)
            await catalog_acknowledgement()
            assert await browser.js("""Array.from(
                document.querySelectorAll('#setlist li:not(.pause) .song-name'))
                .every(n=>n.textContent==='Комета обновлённая')""")
            assert (
                await browser.js("document.querySelector('#setlist .pause .song-name').textContent")
                == ""
            )
            # A catalogue drag onto the WHOLE footer button appends, for mouse and touch.
            await browser.js("""
                document.getElementById('search').value='Звёздный';
                document.getElementById('search').dispatchEvent(new Event('input'));
                document.getElementById('add-pause-bottom').scrollIntoView({block:'center'});
            """)
            await browser.drag(".catalog-entry .handle", "#add-pause-bottom", touch=public_launch)
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 4
            assert (
                await browser.js(
                    "document.querySelector('#setlist li:last-child .song-name').textContent"
                )
                == "Звёздный ветер"
            )
            assert not await browser.js("document.getElementById('pause-dialog').open")
            await browser.wait(
                "document.getElementById('status').textContent.includes('Сохранено')"
            )
            async with PostgresUnitOfWork(engine) as uow:
                saved, _, _ = await Setlists(uow.store, world.admin).read(
                    world.group.id, world.concert.id, private=True
                )
                assert [
                    item["title"] for item in saved["blocks"][0]["items"] if item.get("song_id")
                ] == ["Комета обновлённая", "Комета обновлённая", "Звёздный ветер"]
            # The ordinary click is still a pause dialog, independent of drag behavior.
            await asyncio.sleep(0.55)
            await browser.js("document.getElementById('add-pause-bottom').click()")
            assert await browser.js("document.getElementById('pause-dialog').open")
            await browser.js("document.getElementById('pause-cancel').click()")
            await browser.js("""
                document.getElementById('planned-edit').click();
                document.getElementById('planned-value').value='1:15';
                document.getElementById('planned-form').requestSubmit();
            """)
            await browser.wait("document.getElementById('planned-dialog').open")
            for _ in range(80):
                async with PostgresUnitOfWork(engine) as uow:
                    requests = await uow.store.find(Outbox, support_kind="catalog", sent=False)
                if requests:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Planned duration request was not queued")
            async with Bot("123456:TEST", session=FakeTelegram()) as bot:
                assert await deliver_one_outbox(bot, engine)
            await browser.wait("!document.getElementById('planned-dialog').open")
            assert (
                await browser.js("document.getElementById('planned-edit').textContent") == "75:00"
            )
            assert "запас" in await browser.js(
                "document.getElementById('planned-delta').textContent"
            )
            await browser.js("""
                document.getElementById('search').value='Меланхолия';
                document.getElementById('search').dispatchEvent(new Event('input'));
                document.querySelector('.catalog-entry .song-edit').click();
            """)
            await browser.wait("document.getElementById('song-dialog').open")
            assert await browser.js("!document.getElementById('song-delete').hidden")
            assert await browser.js("document.getElementById('song-title').value") == "Меланхолия"
            await browser.js("""(() => {
                const ask = window.confirm;
                window.confirm = () => false;
                document.getElementById('song-delete').click();
                window.confirm = ask;
            })()""")
            assert await browser.js("document.getElementById('song-dialog').open")
            async with PostgresUnitOfWork(engine) as uow:
                assert not await uow.store.find(Outbox, support_kind="catalog", sent=False)
            await browser.js("document.getElementById('song-delete').click()")
            for _ in range(80):
                async with PostgresUnitOfWork(engine) as uow:
                    requests = await uow.store.find(Outbox, support_kind="catalog", sent=False)
                if requests:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Archive request was not queued")
            async with Bot("123456:TEST", session=FakeTelegram()) as bot:
                assert await deliver_one_outbox(bot, engine)
            await browser.wait("!document.getElementById('song-dialog').open")
            assert "архив" in await browser.js("document.getElementById('status').textContent")
            assert not await browser.js("!!document.querySelector('.catalog-entry')")
            await browser.js("document.getElementById('publish').click()")
            for _ in range(50):
                async with PostgresUnitOfWork(engine) as uow:
                    pending = await uow.store.find(Outbox, sent=False)
                if pending:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Publication was not queued")
            assert not await browser.js("window.testClosed === true")
            bot = Bot("123456:TEST", session=FakeTelegram())
            assert await deliver_one_outbox(bot, engine)
            await browser.wait("location.search.includes('view=performance')")
            await browser.wait(
                "document.getElementById('workspace-status')?.textContent"
                ".includes('Ваш вариант отправлен')"
            )
            await bot.session.close()
    finally:
        if browser:
            browser.reader.cancel()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        await server.close()


async def browser_manage_performance(browser, engine, group_id, origin):
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&band={group_id}"})
    await browser.click_named("#workspace-content button", "Новое выступление")
    await browser.wait("!!document.getElementById('workspace-performance-performance_type')")
    await browser.click_named("dialog[open] button", "История")
    await browser.click_named("dialog[open] button", "Взять за шаблон")
    await browser.wait("!document.querySelector('dialog[aria-label=История]')")
    assert await browser.js("document.getElementById('workspace-performance-date').value") == ""
    assert await browser.js("document.getElementById('workspace-performance-title').value") == (
        "Прошлое выступление"
    )
    await browser.click_named("dialog[open] button", "Назад")
    await browser.wait("!document.getElementById('workspace-performance-performance_type')")
    await browser.click_named("#workspace-content button", "Новое выступление")
    await browser.wait("!!document.getElementById('workspace-quick-performance')")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 320, "height": 640, "deviceScaleFactor": 1, "mobile": True},
    )
    for theme in ("light", "dark"):
        await browser.js(f"document.documentElement.dataset.theme = '{theme}'")
        await browser.assert_button_text_fits()
        assert await browser.js("""(() => {
            const box = document.getElementById('workspace-quick-performance');
            return box.offsetWidth === 20 && box.closest('label').offsetHeight >= 44;
        })()""")
        assert await browser.js("document.documentElement.scrollWidth <= innerWidth")
    assert await browser.js("""(() => {
        const dialog = document.querySelector('dialog[open]');
        const style = document.documentElement.style;
        style.setProperty('--tg-safe-area-inset-top', '24px');
        style.setProperty('--tg-content-safe-area-inset-top', '12px');
        const respectsInset = dialog.getBoundingClientRect().top >= 36;
        dialog.scrollTop = dialog.scrollHeight;
        const close = dialog.querySelector('.sheet-heading button').getBoundingClientRect();
        const visible = close.top >= dialog.getBoundingClientRect().top
            && close.bottom <= innerHeight;
        dialog.scrollTop = 0;
        style.removeProperty('--tg-safe-area-inset-top');
        style.removeProperty('--tg-content-safe-area-inset-top');
        return respectsInset && visible;
    })()""")
    await browser.js("document.documentElement.dataset.theme = 'light'")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 900, "deviceScaleFactor": 1, "mobile": True},
    )
    await browser.js("""
        document.getElementById('workspace-performance-date').value = '2099-06-05';
        document.getElementById('workspace-performance-title').value = 'Летний концерт';
        for (const type of ['city', 'performance_type']) {
          const select = document.getElementById('workspace-performance-' + type);
          select.value = 'other';
          select.dispatchEvent(new Event('change'));
          document.getElementById(select.id + '-custom').value =
            type === 'city' ? 'Тула' : 'Репетиция';
        }
    """)
    assert await browser.js("""
        (() => {
          const [review, back] = document.querySelectorAll('form .performance-actions button');
          const select = document.getElementById('workspace-performance-city');
          return review.offsetWidth === back.offsetWidth && back.offsetWidth === select.offsetWidth
            && Math.abs(review.offsetHeight - select.offsetHeight) <= 1;
        })()
    """)
    await browser.click_named("dialog[open] button", "Сверить и создать")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('dialog[open] dl')?.innerText.includes('Летний концерт')"
    )
    assert await browser.js(
        "document.querySelector('dialog[open] dl').innerText.includes('Репетиция')"
    )
    await browser.click_named("dialog[open] button", "Подтвердить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content h2')?.textContent === 'Летний концерт'"
    )
    await browser.click_named("#workspace-content a", "Тайминг выступления")
    await browser.wait("!!document.querySelector('.timing-form')")
    await browser.click_named(".timing-form button", "Добавить строку")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 320, "height": 640, "deviceScaleFactor": 1, "mobile": True},
    )
    await browser.assert_button_text_fits()
    assert await browser.js("document.documentElement.scrollWidth <= innerWidth")
    await browser.js("""
        document.querySelector('.timing-form [name=start]').value = '17:00';
        document.querySelector('.timing-form [name=end]').value = '17:45';
        document.querySelector('.timing-form [name=label]').value = 'Саундчек';
    """)
    await browser.click_named(".timing-form button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.getElementById('workspace-status').textContent === 'Тайминг сохранён.'"
    )
    assert (
        await browser.js("document.querySelector('.timing-form [name=label]').value") == "Саундчек"
    )
    await browser.click_named("#workspace-content a", "Назад")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 390, "height": 900, "deviceScaleFactor": 1, "mobile": True},
    )
    await browser.click_named("#workspace-content button", "Данные выступления")
    await browser.js("document.getElementById('workspace-performance-city').value = 'Казань'")
    await browser.click_named("dialog[open] button", "Предпросмотр")
    await process_catalog_request(engine)
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait("document.querySelector('#workspace-content')?.innerText.includes('Казань')")
    await browser.click_named("#workspace-content button", "Удалить выступление")
    await process_catalog_request(engine)
    await browser.click_named("dialog[open] button", "Подтвердить")
    await process_catalog_request(engine)
    await browser.click_named("#workspace-content a", "Корзина выступлений")
    await browser.wait(
        "document.querySelector('.workspace-list h3')?.textContent === 'Летний концерт'"
    )
    await browser.js("document.querySelector('.workspace-list li button').click()")
    await process_catalog_request(engine)
    await browser.click_named("dialog[open] button", "Подтвердить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content h2')?.textContent === 'Летний концерт'"
    )
    assert await browser.js(
        "document.querySelector('#workspace-content').innerText.includes('Казань')"
    )


async def browser_team(browser, engine, group_id, origin):
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&band={group_id}"})
    await browser.click_named("#workspace-content a", "Постоянная команда")
    await browser.click_named("#workspace-content button", "+ Добавить участников")
    await browser.js("""
        document.getElementById('team-recipient').value = '3';
        document.getElementById('team-roles-input').value = 'Световик';
    """)
    await browser.click_named("dialog[open] button", "Предпросмотр")
    await browser.wait("!!document.querySelector('dialog[open] a.profile-link[href$=\"id=3\"]')")
    await browser.click_named("dialog[open] button", "+ Добавить участников")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content')?.innerText.includes('Световик')"
    )
    await browser.call(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": "window.Telegram.WebApp.initData = " + json.dumps(signed(actor=3)),
        },
    )
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1"})
    await browser.wait(
        "document.getElementById('workspace-status')?.textContent === 'У вас пока нет коллективов.'"
    )
    await browser.click_named("#workspace-content a", "Приглашения в команду")
    await browser.wait(
        "document.querySelector('#workspace-content')?.innerText.includes('Световик')"
    )
    await browser.click_named("#workspace-content button", "Вступить в коллектив")
    await browser.click_named("dialog[open] button", "Подтвердить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content')?.innerText"
        ".includes('Приглашений в команду пока нет.')"
    )
    await browser.click_named("#workspace-content a", "Мои коллективы")
    await browser.click_named(".workspace-list a", "Коллектив А")
    await browser.click_named("#workspace-content a", "Постоянная команда")
    await browser.wait("document.querySelector('.workspace-list')?.innerText.includes('Световик')")
    assert await browser.js(
        "!document.querySelector('#workspace-content').innerText.includes('+ Добавить участников')"
    )
    await browser.click_named("#workspace-content a", "К коллективу")
    await browser.click_named("#workspace-content a", "⚙️ Настройки коллектива")
    await browser.wait(
        "document.querySelector('#workspace-content dl')?.innerText.includes('Таблица Article')"
    )
    assert not await browser.js("document.querySelector('#workspace-content button')")


async def browser_staff_directory(browser, engine, group_id, origin):
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&band={group_id}"})
    await browser.click_named("#workspace-content a", "Постоянная команда")
    await browser.click_named("#workspace-content a", "Знакомые исполнители")
    await browser.click_named("#workspace-content button", "＋ Добавить исполнителя")
    await browser.js("""
        document.getElementById('staff-contact-role').value = 'Гитара';
        document.getElementById('staff-contact-account').value = '3';
    """)
    await browser.click_named("dialog[open] button", "Предпросмотр")
    await browser.wait("!!document.querySelector('dialog[open] a.profile-link[href$=\"id=3\"]')")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.click_named(
        "#workspace-content button", "Подбор на выступления · Можно приглашать"
    )
    await browser.js("document.getElementById('staff-contact-preference').value = 'preferred'")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content')?.innerText.includes('Предлагать первым')"
    )
    await browser.click_named("#workspace-content a", "Знакомые исполнители")
    await browser.click_named("#workspace-content a", "Составы")
    await browser.click_named("#workspace-content button", "＋ Создать состав")
    await browser.js(r"""
        document.getElementById('staff-lineup-name').value = 'Акустика';
        document.getElementById('staff-lineup-quick').click();
        document.getElementById('staff-lineup-paste').value = 'Гитара: 3\nВокал';
    """)
    await browser.click_named("dialog[open] button", "Предпросмотр")
    await browser.wait("!!document.querySelector('dialog[open] a.profile-link[href$=\"id=3\"]')")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content h2')?.textContent === 'Акустика'"
    )
    await browser.click_named("#workspace-content button", "Редактировать состав")
    await browser.js("document.querySelector('[data-move=down]:enabled').click()")
    await browser.click_named("dialog[open] button", "Предпросмотр")
    await browser.wait("document.querySelector('dialog[open] li strong')?.textContent === 'Вокал'")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content li strong')?.textContent === 'Вокал'"
    )


async def browser_roster(browser, engine, world, origin):
    await browser.call(
        "Page.navigate",
        {
            "url": f"{origin}/miniapp/?workspace=1&band={world.group.id}"
            f"&view=performance&performance={world.concert2.id}",
        },
    )
    await browser.click_named("#workspace-content a", "👥 Состав выступления")
    await browser.click_named("#workspace-content button", "Изменить состав выступления")
    assert not await browser.js("document.querySelector('dialog[open] [data-move]')")
    await browser.js(r"""
        document.getElementById('staff-lineup-quick').click();
        document.getElementById('staff-lineup-paste').value = 'Гитара: 3\nВокал: 1';
    """)
    await browser.click_named("dialog[open] button", "Предпросмотр")
    await browser.wait("!!document.querySelector('dialog[open] a.profile-link[href$=\"id=3\"]')")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.click_named("#workspace-content button", "Проверить занятость")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('dialog[open]')?.innerText.includes('Конфликтов не найдено')"
    )
    await browser.click_named("dialog[open] button", "Пригласить · 1 чел.")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content')?.innerText.includes('Ждём ответа')"
        " && document.querySelector('#workspace-content')?.innerText.includes('Подтверждено')"
    )
    await browser.click_named("#workspace-content a", "К выступлению")
    await browser.wait(
        "document.querySelector('#workspace-content h2')?.textContent === 'Второй концерт'"
    )


async def browser_guest_gig(browser, engine, world, origin):
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        await lists.edit_songs(world.group.id, world.concert2.id, 0, ["Комета"])
        await lists.approve(world.group.id, world.concert2.id, 1)
    script = await browser.call(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": "window.Telegram.WebApp.initData = " + json.dumps(signed(actor=3)),
        },
    )
    try:
        await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1"})
        await browser.wait(
            "document.getElementById('workspace-status')?.textContent "
            "=== 'У вас пока нет коллективов.'"
        )
        await browser.click_named("#workspace-content a", "Моё расписание")
        await browser.click_named("#workspace-content a", "📩 Приглашения")
        await browser.click_named(".workspace-list a", "Коллектив А · Второй концерт")
        await browser.click_named("#workspace-content button", "✅ Подтвердить участие")
        await browser.click_named("dialog[open] button", "Подтвердить")
        await process_catalog_request(engine)
        await browser.click_named("#workspace-content a", "Открыть сет-лист")
        await browser.wait(
            "document.querySelector('.shared-setlist')?.innerText.includes('Комета')"
        )
        await browser.click_named("#workspace-content button", "Экспорт")
        await browser.click_named("dialog[open] button", "Скачать PDF")
        await process_catalog_request(engine)
        await browser.wait("!!document.querySelector('dialog[open] a[download]:not([hidden])')")
        await browser.click_named("dialog[open] button", "Закрыть")
        await browser.click_named("#workspace-content a", "К приглашению")
        await browser.click_named("#workspace-content a", "Моё расписание")
        await browser.click_named("#workspace-content a", "✅ Подтверждённые участия")
        await browser.wait(
            "document.querySelector('.workspace-list')?.innerText.includes('Участие подтверждено')"
        )
        await browser.click_named(".workspace-list a", "Коллектив А · Второй концерт")
        await browser.click_named("#workspace-content button", "Запросить подмену")
        await browser.js(
            "document.getElementById('replacement-candidate').value = '@StageCandidate'"
        )
        await browser.click_named("dialog[open] button", "Предложить кандидата")
        await process_catalog_request(engine)
        await browser.wait(
            "document.querySelector('#workspace-content')?.innerText"
            ".includes('Запрос на подмену отправлен.')"
        )
    finally:
        await browser.call("Page.removeScriptToEvaluateOnNewDocument", script)


async def browser_approve_self_substitution(browser, engine, world, origin):
    await browser.call(
        "Page.navigate",
        {
            "url": f"{origin}/miniapp/?workspace=1&band={world.group.id}"
            f"&view=lineup&performance={world.concert2.id}",
        },
    )
    await browser.wait(
        "document.getElementById('workspace-content')?.textContent.includes('@stagecandidate')"
    )
    await browser.click_named("#workspace-content button", "Проверить кандидата")
    await browser.click_named("dialog[open] button", "Создать ссылку-приглашение")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('dialog[open] input[readonly]')?.value.includes('?start=swap_')"
    )
    await browser.click_named("dialog[open] button", "Закрыть")
    await browser.click_named("#workspace-content button", "Подобрать подмену")
    await process_catalog_request(engine)
    await browser.click_named("dialog[open] button", "Назначить себя")
    await process_catalog_request(engine)
    await browser.click_named("dialog[open] button", "Встать на подмену")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('.workspace-list li')?.innerText"
        ".includes('1 · Подтверждено · Подмена утверждена')"
    )
    assert not await browser.js(
        "document.querySelector('.workspace-list li').innerText.includes('3 · Подтверждено')"
    )


async def browser_schedule(browser, engine, origin):
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&view=my-gigs"})
    await browser.wait(
        "document.querySelector('.workspace-list li')?.innerText.includes('Второй концерт')"
    )
    await browser.click_named("#workspace-content a", "🚫 Недоступные даты")
    await process_catalog_request(engine)
    await browser.click_named("#workspace-content button", "＋ Отметить занятость")
    await browser.js("document.getElementById('busy-starts').value = '2099-06-06'")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await process_catalog_request(engine)
    await browser.wait("document.querySelector('.workspace-list li')?.innerText.includes('2099')")
    await browser.click_named("#workspace-content a", "📅 Подключить календарь")
    runtime = CalendarRuntime("https://example.org", Fernet.generate_key().decode(), KEY)
    await process_catalog_request(engine, calendar=runtime)
    await browser.click_named(".workspace-list li:first-child button", "Подключить")
    await browser.click_named("dialog[open] button", "Подтвердить")
    await process_catalog_request(engine, calendar=runtime)
    await browser.wait(
        "document.getElementById('calendar-link')?.value.startsWith('https://example.org/calendar/')"
    )
    assert await browser.js("document.getElementById('calendar-link').readOnly")
    await browser.click_named("dialog[open] button", "Закрыть")
    await process_catalog_request(engine, calendar=runtime)
    await browser.click_named(".workspace-list li:first-child button", "Показать ссылку")
    await process_catalog_request(engine, calendar=runtime)
    await browser.wait("document.getElementById('calendar-link')?.value.endsWith('.ics')")
    await browser.click_named("dialog[open] button", "Закрыть")


async def browser_profile(browser, engine, origin):
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1"})
    await browser.click_named("#workspace-content a", "👤 Мой профиль")
    await browser.wait("!!document.querySelector('.profile-nav')")
    assert await browser.js("""
        const links = document.querySelectorAll('#workspace-content > :last-child a');
        links.length === 2 && links[0].textContent.includes('расписание')
            && links[1].textContent.includes('Назад')
    """)
    await browser.js("document.querySelector('.profile-nav a').click()")
    await browser.wait("!!document.querySelector('a[aria-current]')")
    await browser.js("window.nativeBack()")
    await browser.click_named("#workspace-content button", "✏️ Изменить")
    await browser.js("document.getElementById('profile-name').value = 'Имя на сцене'")
    await browser.js(
        "const roles = document.getElementById('profile-roles'); "
        "roles.value = 'басист\\nзвук'; roles.dispatchEvent(new Event('input'))"
    )
    await browser.click_named("dialog[open] button", "Уточнить роли")
    await browser.wait("!!document.getElementById('profile-role-1')")
    assert await browser.js("document.getElementById('profile-role-1').value === ''")
    await browser.js("document.getElementById('profile-role-1').value = 'Звукорежиссёр FOH'")
    await browser.click_named("dialog[open] button", "✅ Сохранить профиль")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content dl')?.innerText.includes('Имя на сцене')"
    )
    assert await browser.js(
        "document.querySelector('#workspace-content dl').innerText.includes("
        "'Бас-гитара, Звукорежиссёр FOH')"
    )
    await browser.click_named("#workspace-content button", "Оформление PDF")
    await browser.js("document.getElementById('profile-pdf-size').value = '30'")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-content dl')?.innerText.includes('30 pt')"
    )
    await browser.click_named("#workspace-content button", "Язык интерфейса")
    await browser.js("document.getElementById('profile-language').value = 'en'")
    await browser.click_named("dialog[open] button", "Сохранить")
    await process_catalog_request(engine)
    await browser.wait("document.documentElement.lang === 'en'")
    await browser.click_named("#workspace-content button", "Interface language")
    await browser.js("document.getElementById('profile-language').value = 'ru'")
    await browser.click_named("dialog[open] button", "Save")
    await process_catalog_request(engine)
    await browser.wait("document.documentElement.lang === 'ru'")


async def browser_band_settings(browser, engine, group_id, origin):
    await browser.call(
        "Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&band={group_id}&past=1"}
    )
    await browser.click_named("#workspace-content a", "⚙️ Настройки коллектива")
    await browser.wait("!!document.querySelector('#workspace-content a[href*=startgroup]')")
    assert await browser.js(
        f"document.querySelector('a[href$=\"?start=transfer_{group_id.hex}\"]')"
        "?.textContent === 'Перенести в другой чат'"
    )
    assert await browser.js(
        f"document.querySelector('a[href$=\"?startgroup=cleanup_{group_id.hex}\"]')"
        "?.textContent === 'Прибраться в чате'"
    )
    await browser.click_named("#workspace-content button", "Изменить настройки")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": 320,
            "height": 640,
            "deviceScaleFactor": 1,
            "mobile": True,
        },
    )
    await browser.js("""
        const long = document.createElement('button');
        long.type = 'button';
        long.id = 'long-label-check';
        long.textContent = 'Восстановить коллектив: ' + 'ОченьДлинноеНазвание'.repeat(10);
        document.querySelector('dialog[open] .toolbar').append(long);
    """)
    for theme in ("light", "dark"):
        await browser.js(f"document.documentElement.dataset.theme = '{theme}'")
        await browser.assert_button_text_fits()
        assert await browser.js("document.documentElement.scrollWidth <= innerWidth")
    await browser.js("document.getElementById('long-label-check').remove()")
    await browser.js("document.documentElement.dataset.theme = 'light'")
    await browser.call(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": 390,
            "height": 900,
            "deviceScaleFactor": 1,
            "mobile": True,
        },
    )
    await browser.js("""
        document.getElementById('band-setting-setlist_style').value = 'article';
        document.getElementById('band-setting-language').value = 'en';
        document.getElementById('band-setting-default_pause_seconds').value = '13';
        document.getElementById('band-setting-reminders_enabled').value = 'false';
    """)
    await browser.click_named("dialog[open] button", "Сохранить настройки")
    await process_catalog_request(engine)
    await browser.wait(
        "document.querySelector('#workspace-status')?.textContent"
        " === 'Настройки коллектива сохранены.'"
    )
    assert await browser.js("""
        const content = document.querySelector('#workspace-content');
        ['Таблица Article', '13', 'English', 'Отключены']
            .every(value => content.innerText.includes(value))
    """)
    assert await browser.js("document.documentElement.lang === 'ru'")
    await browser.click_named("#workspace-content a", "◀️ К выступлениям")
    await browser.wait("document.querySelector('[aria-current=page]')?.textContent === 'Прошедшие'")


async def browser_help(browser, engine, actor, origin):
    async with PostgresUnitOfWork(engine) as uow:
        await Support(uow.store, actor).configure(-900, "shigarovms")
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1"})
    await browser.wait("!!document.querySelector('a[href$=\"?startgroup=connect_band\"]')")
    await browser.js("window.Telegram.WebApp.openTelegramLink = url => { window.chatLink = url; }")
    await browser.click_named("#workspace-content a", "🎸 Подключить коллектив")
    assert await browser.js("window.chatLink.endsWith('?startgroup=connect_band')")
    await browser.click_named("#workspace-content summary", "Как вступить в коллектив")
    assert await browser.js(
        "document.querySelector('details[open] p')?.textContent.includes('/register')"
    )
    await browser.click_named("#workspace-content a", "Справка и поддержка")
    await browser.click_named("#workspace-content summary", "Репертуар")
    assert await browser.js(
        "document.querySelector('details[open] > p').innerText.includes('алиасам')"
    )
    await browser.click_named("#workspace-content details[open] summary", "Команды Telegram")
    assert await browser.js(
        "document.querySelector('details[open] details[open] .workspace-help-commands')"
        "?.textContent.includes('/catalog')"
    )
    await browser.click_named("#workspace-content button", "Связаться с поддержкой")
    await browser.js("document.getElementById('support-message').value = 'Вопрос о репертуаре'")
    await browser.click_named("dialog[open] button", "Отправить обращение")
    await process_catalog_request(engine)
    await browser.wait(
        "document.getElementById('workspace-status').textContent.includes('Обращение принято')"
    )
    async with PostgresUnitOfWork(engine) as uow:
        (event,) = await uow.store.find(Outbox, support_kind="request")
        assert event.chat_id == -900 and "Вопрос о репертуаре" in event.text


async def browser_band_trash(browser, engine, band, origin):
    await browser.call("Page.navigate", {"url": f"{origin}/miniapp/?workspace=1&band={band}"})
    await browser.click_named("#workspace-content a", "⚙️ Настройки коллектива")
    await browser.click_named("#workspace-content button", "Переместить коллектив в корзину")
    await browser.click_named("dialog[open] button", "Продолжить")
    await process_catalog_request(engine)
    await browser.wait("!!document.getElementById('band-trash-name')")
    await browser.js("document.getElementById('band-trash-name').value = 'Коллектив Б'")
    await browser.click_named("dialog[open] button", "Переместить в корзину")
    await process_catalog_request(engine)
    await browser.wait("location.search.includes('view=band-trash')")
    await browser.click_named("#workspace-content button", "Восстановить коллектив: Коллектив Б")
    await browser.click_named("dialog[open] button", "Подтвердить")
    await process_catalog_request(engine)
    await browser.wait(
        "document.getElementById('workspace-status').textContent.includes('восстановлен')"
    )
    await browser.click_named("#workspace-content a", "Мои коллективы")
    await browser.wait(
        "document.querySelector('.workspace-list')?.innerText.includes('Коллектив Б')"
    )
