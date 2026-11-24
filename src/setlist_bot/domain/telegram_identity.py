"""Telegram handles are mutable labels; numeric IDs remain the authority."""

import re

from setlist_bot.domain.entities import User
from setlist_bot.domain.rules import require

USERNAME = re.compile(r"[a-zA-Z0-9_]{1,32}\Z", re.ASCII)


def normalized_username(value: str) -> str:
    name = value.strip().removeprefix("@")
    require(bool(USERNAME.fullmatch(name)), "input")
    return name.casefold()


def person_caption(person: User, *, registered: bool = False, fallback: str | None = None) -> str:
    name = person.profile.get("name") or person.display_name
    handle = f"@{person.username}" if person.username else ""
    if (registered or person.profile.get("name")) and name and handle:
        return f"{name} ({handle})"
    return handle or name or (str(person.telegram_id) if fallback is None else fallback)


def fallback_profile(person: User) -> str | None:
    if not (person.profile.get("name") or person.display_name or person.username):
        return f"tg://user?id={person.telegram_id}"
    return None
