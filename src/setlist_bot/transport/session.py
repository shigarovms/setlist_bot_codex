import ssl

from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientSession, TCPConnector


class VerifiedSession(AiohttpSession):
    """Honor standard proxy/CA settings while retaining certificate and hostname checks."""

    def __init__(self) -> None:
        super().__init__()
        self.http: ClientSession | None = None

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
