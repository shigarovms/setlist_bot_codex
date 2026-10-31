import logging

from aiohttp import web

from setlist_bot.calendar.app import create_app
from setlist_bot.calendar.settings import CalendarSettings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = CalendarSettings()
    # RU/EN: Disable access logs: the secret is part of the URL, not a login cookie.
    web.run_app(
        create_app(settings), host=settings.host, port=settings.port, access_log=None, print=None
    )


if __name__ == "__main__":
    main()
