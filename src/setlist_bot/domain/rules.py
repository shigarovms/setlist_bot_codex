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


def effective_pause(snapshot: dict[str, Any], item: dict[str, Any], *, first: bool = False) -> int:
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
    snapshot = pause_content(snapshot)
    blocks = snapshot["blocks"]
    times = [
        sum(item["duration_seconds"] for item in block["items"])
        + sum(
            effective_pause(snapshot, item, first=pos == 0)
            for pos, item in enumerate(block["items"])
        )
        for block in blocks
    ]
    breaks = sum(block["break_seconds"] for block in blocks[:-1])
    return times, sum(times), sum(times) + breaks
