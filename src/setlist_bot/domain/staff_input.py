"""Role/person input: anchor the account at the end, preserving hyphens in roles."""

import re

from setlist_bot.domain.rules import require

PERSON = re.compile(r"^(.*?)(?:\s*[-:]\s*|\s+)(@[A-Za-z0-9_]{1,32}|[0-9]+)$")


def role_person(value: str, *, required: bool = False) -> tuple[str, str]:
    text = value.strip()
    match = PERSON.fullmatch(text)
    name, person = (match[1].strip(), match[2]) if match else (text, "")
    require(bool(name) and len(name) <= 100 and not any(ord(c) < 32 for c in name), "input")
    require(not required or bool(person), "input")
    # RU: Незаконченный @ник не превращаем в название новой роли.
    # EN: Reject malformed account references instead of silently creating a role.
    require("@" not in name, "input")
    return name, person
