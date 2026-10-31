import logging

from aiohttp import web

from setlist_bot.miniapp.app import create_app
from setlist_bot.miniapp.settings import MiniAppSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = MiniAppSettings()
    web.run_app(
        create_app(settings), host=settings.host, port=settings.port, access_log=None, print=None
    )


if __name__ == "__main__":
    main()
