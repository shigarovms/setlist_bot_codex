import ssl
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.methods.base import TelegramMethod, TelegramType
from aiohttp import ClientSession, TCPConnector

if TYPE_CHECKING:
    from setlist_bot.infrastructure.telemetry import Monitor


class VerifiedSession(AiohttpSession):
    """Honor standard proxy/CA settings while retaining certificate and hostname checks."""

    def __init__(self, monitor: "Monitor | None" = None) -> None:
        super().__init__()
        self.http: ClientSession | None = None
        self.monitor = monitor

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        try:
            result = await super().make_request(bot, method, timeout)
        except Exception as exc:
            if self.monitor:
                self.monitor.set("telegram_api", "retrying", error=type(exc).__name__)
            raise
        if self.monitor:
            # RU: Даже пустой успешный getUpdates подтверждает связь с Telegram.
            # EN: An empty successful long poll is evidence of reachability, not user activity.
            self.monitor.set("telegram_api", "running", success=True)
        return result

    async def create_session(self) -> ClientSession:
        if self.http is None or self.http.closed:
            self.http = ClientSession(
                connector=TCPConnector(ssl=ssl.create_default_context()), trust_env=True
            )
        return self.http

    async def close(self) -> None:
        if self.http is not None:
            await self.http.close()
            self.http = None
