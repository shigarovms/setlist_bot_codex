"""Real-browser check of role editing, login and same-origin signed form submission."""

import asyncio
import json
import shutil
import socket
import subprocess

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from test_admin import PASSWORD, settings
from test_miniapp_browser import Browser

from setlist_bot.admin.app import create_app
from setlist_bot.domain.entities import ProfessionalRole
from setlist_bot.infrastructure.database import PostgresUnitOfWork


@pytest.mark.skipif(shutil.which("chromium") is None, reason="Chromium is optional locally")
async def test_role_editor_can_save_in_chromium(engine, tmp_path):
    server = TestServer(create_app(settings(engine), engine))
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
            browser = Browser(await client.ws_connect(pages[0]["webSocketDebuggerUrl"]))

            async def wait(expression):
                try:
                    async with asyncio.timeout(20):
                        while not await browser.js(expression):
                            await asyncio.sleep(0.1)
                except TimeoutError as exc:
                    raise AssertionError(
                        await browser.js("({path:location.pathname, text:document.body.innerText})")
                    ) from exc

            await browser.call("Page.enable")
            await browser.call("Page.navigate", {"url": str(server.make_url("/roles"))})
            await wait("document.querySelector('[name=username]') !== null")
            async with client.get(server.make_url("/favicon.ico")) as icon:
                assert icon.status == 204 and "Set-Cookie" not in icon.headers
            await browser.js(
                "document.querySelector('[name=username]').value='admin';"
                "document.querySelector('[name=password]').value=" + json.dumps(PASSWORD) + ";"
                "document.querySelector('form').requestSubmit()"
            )
            await wait("location.pathname === '/' && document.querySelector('h1') !== null")
            await browser.call("Page.navigate", {"url": str(server.make_url("/roles"))})
            await wait("document.querySelector('[name=title]') !== null")
            await browser.js(
                "document.querySelector('[name=title]').value='Технический директор';"
                "document.querySelector('[name=aliases]').value='technical director';"
                "document.querySelector('[name=title]').closest('form').requestSubmit()"
            )
            await wait(
                "location.search.includes('saved') && document.querySelector('table') !== null"
            )
            assert await browser.js("document.body.innerText.includes('Технический директор')")
        async with PostgresUnitOfWork(engine) as uow:
            assert len(await uow.store.find(ProfessionalRole, title="Технический директор")) == 1
    finally:
        if browser:
            browser.reader.cancel()
            await asyncio.gather(browser.reader, return_exceptions=True)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        await server.close()
