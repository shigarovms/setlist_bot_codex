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


def settings(value: dict[str, Any]) -> dict[str, Any]:
    result = {**DEFAULTS, **value}
    require(
        result["font"] in FONTS
        and result["size"] in range(24, 41)
        and result["align"] in ("left", "center"),
        "input",
    )
    return result
