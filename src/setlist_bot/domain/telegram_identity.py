"""Telegram handles are mutable labels; numeric IDs remain the authority."""

import re

from setlist_bot.domain.rules import require

USERNAME = re.compile(r"[a-zA-Z0-9_]{1,32}\Z", re.ASCII)


def normalized_username(value: str) -> str:
    name = value.strip().removeprefix("@")
    require(bool(USERNAME.fullmatch(name)), "input")
    return name.casefold()
