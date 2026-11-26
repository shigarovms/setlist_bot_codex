from typing import Any

from setlist_bot.domain.rules import require

FONTS = {
    "noto": "Noto Sans",
    "open": "Open Sans",
    "dejavu": "DejaVu Sans",
    "liberation": "Liberation Sans",
    "serif": "Noto Serif",
}
DEFAULTS: dict[str, Any] = {"font": "noto", "size": 40, "align": "left"}
MAX_TARGET_PAGES = 200


def settings(value: dict[str, Any]) -> dict[str, Any]:
    result = {**DEFAULTS, **value}
    require(
        result["font"] in FONTS
        and result["size"] in range(24, 41)
        and result["align"] in ("left", "center"),
        "input",
    )
    if "pages" in result:
        require(type(result["pages"]) is int and 1 <= result["pages"] <= MAX_TARGET_PAGES, "input")
    return result


def validated_settings(value: Any) -> dict[str, Any]:
    require(
        isinstance(value, dict)
        and set(value) in ({"font", "size", "align"}, {"font", "size", "align", "pages"})
        and isinstance(value["font"], str)
        and type(value["size"]) is int,
        "input",
    )
    return settings(value)
