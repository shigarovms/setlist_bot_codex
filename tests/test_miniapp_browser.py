"""Real Chromium smoke test; Telegram SDK is stubbed only in the test browser."""

import asyncio
import base64
import json
import shutil
import socket
import subprocess

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from test_miniapp import session, settings, signed

from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import DeploymentIdentity
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


class Browser:
    def __init__(self, ws):
        self.ws, self.sequence, self.pending = ws, 0, {}
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
        for _ in range(100):
            if await self.js(expression):
                return
            await asyncio.sleep(0.05)
        raise AssertionError("Browser condition failed: " + expression)

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


@pytest.mark.skipif(
    shutil.which("chromium") is None, reason="Chromium is optional for local UI check"
)
@pytest.mark.parametrize("public_launch", [False, True])
async def test_phone_editor_drag_history_pause_save_and_live_themes(
    engine, world, tmp_path, public_launch
):
    from datetime import date

    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        lists = Setlists(uow.store, world.admin)
        past = await lists.create(world.group.id, date(2025, 12, 1), "Прошлое выступление")
        _, version, _ = await lists.read(world.group.id, past.id)
        await lists.edit_songs(world.group.id, past.id, version, ["Комета"])
        _, version, _ = await lists.read(world.group.id, past.id)
        await lists.approve(world.group.id, past.id, version)
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
            for _ in range(100):
                try:
                    async with client.get(f"http://127.0.0.1:{port}/json/list") as response:
                        pages = await response.json()
                    break
                except (aiohttp.ClientError, ValueError):
                    await asyncio.sleep(0.05)
            else:
                raise AssertionError("Chromium did not start")
            ws = await client.ws_connect(pages[0]["webSocketDebuggerUrl"])
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
                onEvent:(name,fn)=>{window.themeChange=fn},ready:()=>{},expand:()=>{},
                close:()=>{window.testClosed=true},
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
            await browser.call(
                "Page.navigate",
                {
                    "url": origin
                    + "/miniapp/"
                    + ("" if public_launch else "#session=" + str(identifier)),
                },
            )
            await browser.wait("document.querySelectorAll('.catalog-entry').length===1")
            await browser.js(
                "document.querySelector('.add').click(); document.querySelector('.add').click()"
            )
            # No manual Save: exercise the debounce and real HTTP autosave.
            await browser.wait(
                "document.getElementById('status').textContent.includes('Сохранено')"
            )
            await browser.js(
                "document.getElementById('add-pause').click(); "
                "document.getElementById('pause-value').value='3'; "
                "document.getElementById('pause-form').requestSubmit()"
            )
            await browser.wait("document.querySelectorAll('#setlist li').length===3")
            assert await browser.js("document.getElementById('total').textContent") == "10:21"
            await browser.drag(
                "#setlist li:last-child .handle", "#setlist li:first-child", touch=True
            )
            assert await browser.js(
                "document.querySelector('#setlist li').classList.contains('pause')"
            )
            # Theme changes must not lose the in-progress list.
            await browser.js("window.Telegram.WebApp.colorScheme='dark'; window.themeChange()")
            assert await browser.js("document.documentElement.dataset.theme") == "dark"
            screenshot = await browser.call("Page.captureScreenshot", {"format": "png"})
            (tmp_path / "miniapp-dark.png").write_bytes(base64.b64decode(screenshot["data"]))
            await browser.js("document.getElementById('older').click()")
            await browser.wait("!document.getElementById('use-history').hidden")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 1
            await browser.js("document.getElementById('newer').click()")
            await browser.wait("document.getElementById('use-history').hidden")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 3
            await browser.drag("#setlist li:first-child .handle", "#remove-zone")
            assert await browser.js("document.querySelectorAll('#setlist li').length") == 2
            await browser.js("document.getElementById('undo').click()")
            await browser.js("document.getElementById('save').click()")
            await browser.wait(
                "document.getElementById('save').disabled && "
                "!document.getElementById('publish').disabled"
            )
            await browser.js("window.Telegram.WebApp.colorScheme='light'; window.themeChange()")
            assert await browser.js("document.documentElement.dataset.theme") == "light"
            screenshot = await browser.call("Page.captureScreenshot", {"format": "png"})
            (tmp_path / "miniapp-light.png").write_bytes(base64.b64decode(screenshot["data"]))
            assert await browser.js("document.documentElement.scrollWidth<=innerWidth")
            async with PostgresUnitOfWork(engine) as uow:
                saved, _, _ = await Setlists(uow.store, world.admin).read(
                    world.group.id, world.concert.id, private=True
                )
                assert len(saved["blocks"][0]["items"]) == 3
                assert not (
                    await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
                )[0]["blocks"][0]["items"]
            # Explicit publication must wait for the existing Telegram worker acknowledgement.
            from aiogram import Bot
            from test_telegram import FakeTelegram

            from setlist_bot.domain.entities import Outbox
            from setlist_bot.main import deliver_one_outbox

            await browser.js("document.getElementById('publish').click()")
            for _ in range(50):
                async with PostgresUnitOfWork(engine) as uow:
                    pending = await uow.store.find(Outbox)
                if pending:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Publication was not queued")
            assert not await browser.js("window.testClosed === true")
            bot = Bot("123456:TEST", session=FakeTelegram())
            assert await deliver_one_outbox(bot, engine)
            await browser.wait("window.testClosed === true")
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
