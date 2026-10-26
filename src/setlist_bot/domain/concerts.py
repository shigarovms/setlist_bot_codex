"""Human concert dates and labels, independent of Telegram and database."""

import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from setlist_bot.domain.entities import now
from setlist_bot.domain.rules import AppError, normalize

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
EN_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def today() -> date:
    return now().astimezone(ZoneInfo("Europe/Moscow")).date()


def date_label(value: date, language: str = "ru") -> str:
    months = MONTHS if language == "ru" else EN_MONTHS
    label = f"{value.day:02d} {months[value.month - 1]}"
    if value.year != today().year:
        label = f"{value.year} {'год ' if language == 'ru' else ''}{label}"
    return label


def compact_duration(seconds: int, language: str = "ru") -> str:
    hours, minutes = divmod((seconds + 59) // 60, 60)
    h, m = ("ч", "мин") if language == "ru" else ("h", "min")
    return (f"{hours}{h} " if hours else "") + f"{minutes}{m}"


def parse_concert(value: str) -> tuple[date, str]:
    text = value.strip()
    current = today()
    relative = re.match(r"^(сегодня|завтра|послезавтра|today|tomorrow)\b", text, re.I)
    if relative:
        shift = {"сегодня": 0, "today": 0, "завтра": 1, "tomorrow": 1, "послезавтра": 2}
        return current + timedelta(days=shift[relative[1].lower()]), text[relative.end() :].strip()
    numeric = re.match(
        r"^(\d{4}|\d{1,2})\s*([./-])\s*(\d{1,2})(?:\s*[./-]\s*(\d{2}|\d{4}))?(?=\s|$)", text
    )
    try:
        if numeric:
            first, _, second, third = numeric.groups()
            if len(first) == 4:
                if third is None:
                    raise ValueError()
                year, month, day = int(first), int(second), int(third)
            else:
                day, month = int(first), int(second)
                year = int(third) if third else current.year
                if year < 100:
                    year += 2000
            result = date(year, month, day)
            return result, text[numeric.end() :].strip()
        named = re.match(
            r"^(\d{1,2})\s+([А-Яа-яЁёA-Za-z]+)\.?(?:\s+(\d{4})(?:\s*г(?:од(?:а)?)?\.?)?)?(?=\s|$)",
            text,
        )
        if named:
            word = normalize(named[2])
            indexes = [
                i
                for i, name in enumerate(MONTHS, 1)
                if word
                in (
                    name,
                    name[:3],
                    name[:4],
                    name[:-1] + "ь" if i in (1, 2, 4, 6, 7, 9, 10, 11, 12) else name[:-1],
                )
            ]
            if word == "май":
                indexes = [5]
            if not indexes:
                indexes = [
                    i for i, name in enumerate(EN_MONTHS, 1) if name.lower().startswith(word)
                ]
            if len(indexes) != 1:
                raise ValueError()
            return date(
                int(named[3]) if named[3] else current.year, indexes[0], int(named[1])
            ), text[named.end() :].strip()
    except ValueError as exc:
        raise AppError("concert_input") from exc
    raise AppError("concert_input")
