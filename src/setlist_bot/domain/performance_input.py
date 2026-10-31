"""Human field recognition; preview is mandatory before any database write."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from setlist_bot.domain.cities import city_name
from setlist_bot.domain.concerts import parse_concert
from setlist_bot.domain.rules import normalize, parse_pause, require

SEPARATOR = re.compile(r"[ \t]{2,}|(?<!\bг)(?<!\bул)(?<!\bим)(?<!\bпр)(?<!\bд)\.\s+", re.I)
TYPE_ALIASES = {
    "Сольник": (
        "сольник",
        "сольный концерт",
        "концерт",
        "сольное выступление",
        "solo",
        "solo concert",
    ),
    "Корпорат": (
        "корпорат",
        "корп",
        "корпор",
        "заказняк",
        "корпоратив",
        "корпоративное",
        "корпоративное выступление",
        "корпоративный концерт",
        "corporate",
        "private event",
    ),
    "Фест": ("фест", "фестиваль", "festival", "fest"),
}


def performance_seconds(value: str) -> int | None:
    text = value.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        text += ":00"
    return parse_pause(text + " мин" if text.isascii() and text.isdecimal() else text)


@dataclass(frozen=True)
class PerformanceInput:
    date: date
    title: str = ""
    duration: int | None = None
    performance_type: str = ""
    city: str = ""
    info: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "title": self.title,
            "duration": self.duration,
            "performance_type": self.performance_type,
            "city": self.city,
            "info": self.info,
        }


def parse_performance(value: str) -> PerformanceInput:
    when, remainder = parse_concert(value)
    fields: dict[str, Any] = {}
    unknown = []
    types = {
        normalize(alias): label for label, aliases in TYPE_ALIASES.items() for alias in aliases
    }
    for part in SEPARATOR.split(remainder):
        text = part.strip()
        if not text:
            continue
        label, colon, content = text.partition(":")
        explicit = {
            "город": "city",
            "city": "city",
            "название": "title",
            "title": "title",
            "тип": "performance_type",
            "type": "performance_type",
            "инфо": "info",
            "info": "info",
            "длительность": "duration",
            "duration": "duration",
        }.get(normalize(label))
        if explicit and colon:
            field = explicit
            text = content.strip()
            require(bool(text), "concert_input")
            parsed: str | int | None = (
                performance_seconds(text)
                if field == "duration"
                else city_name(text) or text
                if field == "city"
                else types.get(normalize(text), text)
                if field == "performance_type"
                else text
            )
            require(parsed is not None, "pause_duration")
        elif normalize(text) in types:
            field, parsed = "performance_type", types[normalize(text)]
        elif (duration := performance_seconds(text)) is not None:
            field, parsed = "duration", duration
        elif (city := city_name(text)) is not None:
            field, parsed = "city", city
        else:
            unknown.append(text)
            continue
        require(field not in fields or fields[field] == parsed, "concert_fields_conflict")
        fields[field] = parsed
    if unknown and "title" not in fields:
        fields["title"] = unknown.pop(0)
    if unknown:
        fields["info"] = ". ".join([*([fields["info"]] if fields.get("info") else []), *unknown])
    for field in ("title", "city", "performance_type", "info"):
        require(len(fields.get(field, "")) <= (2000 if field == "info" else 200), "concert_input")
        if field != "info":
            require(all(ord(char) >= 32 for char in fields.get(field, "")), "concert_input")
    return PerformanceInput(when, **fields)
