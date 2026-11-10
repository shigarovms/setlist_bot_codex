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
        for _ in range(200):
            if await self.js(expression):
                return
            await asyncio.sleep(0.05)
        page = await self.js("({url: location.href, text: document.body?.innerText.slice(0, 500)})")
        raise AssertionError(f"Browser condition failed: {expression}. Page: {page}")

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


@pytest.mark.skipif(
    shutil.which("chromium") is None, reason="Chromium is optional for local UI check"
)
@pytest.mark.parametrize("public_launch", [False, True])
async def test_phone_editor_drag_history_pause_save_and_live_themes(
    engine, world, tmp_path, public_launch
):
    from datetime import date

    async with PostgresUnitOfWork(engine) as uow:
        from setlist_bot.application.catalog import Catalog

        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Меланхолия", "duration_seconds": 246}
        )
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
                == "+ пауза"
            )
            assert await browser.js(
                "(()=>{const s=getComputedStyle(document.getElementById('add-pause-bottom'));"
                "return s.borderTopStyle==='dashed' && s.backgroundColor==='rgba(0, 0, 0, 0)'})()"
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
            await browser.tap(".catalog-entry .add")
            await browser.wait("document.querySelectorAll('#setlist li').length===1")
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
            await browser.js("window.Telegram.WebApp.colorScheme='dark'; window.themeChange()")
            assert await browser.js("document.documentElement.dataset.theme") == "dark"
            assert await browser.js(transparent_pause)
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
