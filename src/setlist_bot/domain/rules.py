import re
from copy import deepcopy
from typing import Any

from unidecode import unidecode


class AppError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SongListError(AppError):
    def __init__(self, issues: list[tuple[int, str, list[str]]]) -> None:
        self.issues = issues
        super().__init__("song_list_errors")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AppError(code)


def normalize(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def search_key(value: str) -> str:
    return normalize(unidecode(normalize(value)))


def duration(value: str) -> int:
    require(bool(re.fullmatch(r"\d{1,5}:\d{2}", value)), "duration")
    minutes, seconds = map(int, value.split(":"))
    require(seconds < 60 and minutes * 60 + seconds > 0, "duration")
    return minutes * 60 + seconds


def clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def pause_seconds(value: str) -> int:
    require(value.isascii() and value.isdecimal(), "pause_input")
    seconds = int(value)
    require(0 <= seconds <= 86400, "pause_input")
    return seconds


def parse_pause(value: str, *, bare_seconds: bool = False) -> int | None:
    text = normalize(value)
    if re.fullmatch(r"\d+:\d{2}(?::\d{2})?", text):
        parts = list(map(int, text.split(":")))
        require(parts[-1] < 60 and (len(parts) == 2 or parts[-2] < 60), "pause_duration")
        seconds = (
            parts[0] * 60 + parts[1]
            if len(parts) == 2
            else parts[0] * 3600 + parts[1] * 60 + parts[2]
        )
    elif bare_seconds and text.isascii() and text.isdecimal():
        seconds = int(text)
    else:
        pattern = (
            r"(-?\d+)\s*(час(?:а|ов)?|ч|мин(?:ута|уты|уту|ут)?|м|"
            r"сек(?:унда|унды|унду|унд)?|с|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\.?"
        )
        matches = list(re.finditer(pattern, text))
        if not matches:
            return None
        end, seconds = 0, 0
        for match in matches:
            if text[end : match.start()].strip():
                return None
            amount, unit = int(match[1]), match[2]
            require(amount >= 0, "pause_duration")
            scale = (
                3600 if unit.startswith(("ч", "h")) else 60 if unit.startswith(("м", "m")) else 1
            )
            seconds += amount * scale
            end = match.end()
        if text[end:].strip():
            return None
    require(0 < seconds <= 86400, "pause_duration")
    return seconds


def is_pause(item: dict[str, Any]) -> bool:
    return item.get("kind") == "pause"


def editable_content(snapshot: dict[str, Any]) -> dict[str, Any]:
    """RU: Сравниваем ввод, а не подписи утверждения/каталог. EN: Compare editable content only."""
    flat = flat_content(snapshot)
    return {
        "default_pause_seconds": flat["default_pause_seconds"],
        "entries": [
            {"pause": item["duration_seconds"]}
            if is_pause(item)
            else {"song_id": item["song_id"], "pause_before": item.get("pause_before")}
            for item in flat["blocks"][0]["items"]
        ],
    }


def pause_item(seconds: int) -> dict[str, Any]:
    require(0 < seconds <= 86400, "pause_duration")
    return {
        "kind": "pause",
        "song_id": None,
        "title": "пауза",
        "duration_seconds": seconds,
        "pause_before": 0,
        "bpm": None,
        "lights": "",
        "comment": "",
        "song_version": None,
    }


def pause_content(content: dict[str, Any]) -> dict[str, Any]:
    """Read previous snapshot formats without changing their stored JSON or timing."""
    result = deepcopy(content)
    legacy_zeroes = "default_pause_seconds" not in result
    result.setdefault("default_pause_seconds", 0)
    for index, block in enumerate(result["blocks"]):
        block.setdefault(
            "kind",
            "encore"
            if normalize(block.get("title", "")) in ("бис", "encore")
            else "main"
            if index == 0
            else "regular",
        )
        previous = None
        for item in block["items"]:
            if "pause_after" in item:
                after = item.pop("pause_after")
                item["pause_before"] = previous
                previous = None if legacy_zeroes and after == 0 else after
    return result


# RU: Адаптер старых снимков не изменяет JSON истории: работаем с глубокой копией.
# EN: Adapt legacy snapshots on a deep copy; never rewrite historical JSON in place.
def flat_content(content: dict[str, Any]) -> dict[str, Any]:
    result = pause_content(content)
    items: list[dict[str, Any]] = []
    blocks = result["blocks"]
    for number, block in enumerate(blocks):
        for position, entry in enumerate(block["items"]):
            if is_pause(entry):
                items.append(pause_item(int(entry["duration_seconds"])))
                continue
            lead = entry.get("pause_before")
            if lead:
                items.append(pause_item(int(lead)))
                entry["pause_before"] = 0
            elif number and position == 0:
                entry["pause_before"] = 0
            items.append(entry)
        if number < len(blocks) - 1 and block["break_seconds"]:
            items.append(pause_item(block["break_seconds"]))
    result["blocks"] = [{"title": "", "kind": "main", "break_seconds": 0, "items": items}]
    return result


def effective_pause(snapshot: dict[str, Any], item: dict[str, Any], *, first: bool = False) -> int:
    if is_pause(item):
        return 0
    override = item["pause_before"]
    return int(
        (0 if first else snapshot.get("default_pause_seconds", 0)) if override is None else override
    )


def validate_song(data: dict[str, Any]) -> None:
    require(isinstance(data.get("title"), str), "song_input")
    require(0 < len(data["title"].strip()) <= 200, "song_input")
    seconds = data.get("duration_seconds")
    require(isinstance(seconds, int) and seconds > 0, "duration")
    bpm = data.get("bpm")
    require(bpm is None or (isinstance(bpm, int) and 0 < bpm <= 1000), "song_input")
    for key in ("lights", "comment"):
        require(isinstance(data.get(key, ""), str) and len(data.get(key, "")) <= 2000, "song_input")


def totals(snapshot: dict[str, Any]) -> tuple[list[int], int, int]:
    snapshot = flat_content(snapshot)
    items = snapshot["blocks"][0]["items"]
    total = sum(
        item["duration_seconds"]
        + effective_pause(snapshot, item, first=pos == 0 or is_pause(items[pos - 1]))
        for pos, item in enumerate(items)
    )
    return [total], total, total
