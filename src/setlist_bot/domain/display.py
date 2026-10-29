"""Presentation-only translations and position labels; catalog values stay untouched."""

import re
from typing import Any

COLORS = {
    "бел": "white",
    "черн": "black",
    "чёрн": "black",
    "красн": "red",
    "розов": "pink",
    "оранжев": "orange",
    "желт": "yellow",
    "жёлт": "yellow",
    "зелен": "green",
    "зелён": "green",
    "син": "blue",
    "голуб": "light blue",
    "фиолетов": "purple",
    "сиренев": "lilac",
    "пурпурн": "purple",
    "малинов": "crimson",
    "бордов": "burgundy",
    "золот": "gold",
    "серебрист": "silver",
    "серебрян": "silver",
    "сер": "gray",
    "бирюзов": "turquoise",
    "бежев": "beige",
    "коричнев": "brown",
    "лазурн": "azure",
    "оливков": "olive",
    "салатов": "lime green",
    "бронзов": "bronze",
    "янтарн": "amber",
}
ENDINGS = r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ую|юю|ого|его|ому|ему|ым|им|ых|их|ыми|ими|ом|ем|ей)"
COLOR_PATTERN = re.compile(
    r"(?<![\w])(" + "|".join(sorted(COLORS, key=len, reverse=True)) + ")" + ENDINGS + r"(?![\w])",
    re.I,
)
BASIC_COLORS = {
    "бордо": "burgundy",
    "индиго": "indigo",
    "лайм": "lime",
    "фуксия": "fuchsia",
    "бирюза": "turquoise",
    "золото": "gold",
    "серебро": "silver",
    "бронза": "bronze",
    "циан": "cyan",
    "маджента": "magenta",
}


def english_colors(text: str, language: str) -> str:
    if language != "en":
        return text
    text = re.sub(
        r"\b(светло|темно|тёмно)[ -]",
        lambda m: "light " if m[1].lower() == "светло" else "dark ",
        text,
        flags=re.I,
    )
    text = COLOR_PATTERN.sub(lambda m: COLORS[m[1].lower()], text)
    return re.sub(
        r"\b(" + "|".join(BASIC_COLORS) + r")\b",
        lambda m: BASIC_COLORS[m[1].lower()],
        text,
        flags=re.I,
    )


def position_label(position: int, count: int) -> str:
    return str(position).zfill(2) if count >= 10 else str(position)


def pause_title(language: str, transliterate: bool = False) -> str:
    return "pause" if language == "en" or transliterate else "пауза"


def entry_labels(items: list[dict[str, Any]]) -> list[str]:
    """RU/EN: Номер считают только песни / pauses never consume a song number."""
    from setlist_bot.domain.rules import is_pause

    count = sum(not is_pause(item) for item in items)
    number = 0
    result = []
    for item in items:
        if is_pause(item):
            result.append("  " if count >= 10 else " ")
        else:
            number += 1
            result.append(position_label(number, count))
    return result


def short_duration(seconds: int, language: str = "ru", *, minutes_only: bool = False) -> str:
    minutes, rest = divmod(seconds, 60)
    m, sec = ("мин", "сек") if language == "ru" else ("min", "sec")
    if minutes_only or minutes:
        return f"{minutes}{m}" + (f"{rest}{sec}" if rest else "")
    return f"{rest}{sec}"
