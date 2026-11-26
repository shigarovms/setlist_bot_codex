"""RU: Локальные названия, не сетевой геокодер. EN: No runtime network calls."""

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

from setlist_bot.domain.rules import normalize

ALIASES = {
    "питер": "Санкт-Петербург",
    "спб": "Санкт-Петербург",
    "петербург": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "saint petersburg": "Санкт-Петербург",
    "мск": "Москва",
    "moscow": "Москва",
    "moskva": "Москва",
    "yerevan": "Ереван",
    "erevan": "Ереван",
}


@lru_cache(maxsize=1)
def city_names() -> dict[str, str]:
    return dict(json.loads((Path(__file__).with_name("data") / "cities.json").read_text()))


def city_name(value: str) -> str | None:
    name = re.sub(r"^(?:г\.|город\b)\s*", "", value.strip(), flags=re.I)
    key = normalize(name)
    return ALIASES.get(key) or city_names().get(key)


@lru_cache(maxsize=1)
def city_zones() -> dict[str, str]:
    source = Path(__file__).with_name("data") / "city-zones.json.gz"
    return dict(json.loads(gzip.decompress(source.read_bytes())))


def city_timezone(value: str) -> str | None:
    name = city_name(value)
    return city_zones().get(normalize(name)) if name else None
