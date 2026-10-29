"""RU: Табличный каталог Telegram. EN: Pure, bounded Article rendering without API calls."""

from dataclasses import dataclass
from typing import Any

from setlist_bot.domain.display import entry_labels, pause_title
from setlist_bot.domain.entities import Song
from setlist_bot.domain.rules import clock, flat_content, is_pause, totals
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.render import song_card


@dataclass
class ArticlePage:
    blocks: list[dict[str, Any]]
    text: str
    buttons: list[tuple[str, str]]


def setlist_articles(heading: str, snapshot: dict[str, Any], language: str) -> list[ArticlePage]:
    """RU: Три столбца, без кнопок в ячейках. EN: Never interpret song titles as markup."""
    items = flat_content(snapshot)["blocks"][0]["items"]
    labels = entry_labels(items)
    _, _, total = totals(snapshot)
    rows = [
        ["№", tr(language, "catalog_title"), tr(language, "catalog_time")],
        *[
            [
                "" if is_pause(item) else label,
                pause_title(language) if is_pause(item) else item["title"],
                clock(item["duration_seconds"]),
            ]
            for label, item in zip(labels, items, strict=True)
        ],
        ["", tr(language, "timing_total", time="").rstrip(), clock(total)],
    ]
    pages = []
    for start in range(1, max(2, len(rows)), 40):
        part = [rows[0], *rows[start : start + 40]]
        cells = [
            [
                {
                    "text": value,
                    "align": "right" if column != 1 else "left",
                    "valign": "top",
                    "is_header": number == 0,
                }
                for column, value in enumerate(row)
            ]
            for number, row in enumerate(part)
        ]
        pages.append(
            ArticlePage(
                [
                    {"type": "paragraph", "text": heading},
                    {
                        "type": "table",
                        "cells": cells,
                        "is_bordered": True,
                        "is_striped": True,
                        "is_compact": True,
                    },
                ],
                heading + "\n\n" + "\n".join("  ".join(row) for row in part[1:]),
                [],
            )
        )
    return pages


def catalog_articles(
    group_name: str, songs: list[Song], buttons: list[tuple[str, str]], language: str
) -> list[ArticlePage]:
    """Keep each table and its card buttons together, including text fallback pages."""
    groups: list[list[Song]] = []
    current: list[Song] = []
    size = 0
    for song in songs:
        length = len(song.title) + len(song.lights) + len(song.comment) + 30
        if current and (len(current) >= 20 or size + length > 8000):
            groups.append(current)
            current, size = [], 0
        current.append(song)
        size += length
    if current:
        groups.append(current)
    pages = []
    offset = 0
    for number, group in enumerate(groups, 1):
        label = tr(language, "catalog_article", page=number, pages=len(groups))
        heading = group_name + "\n" + label
        page_buttons = buttons[offset : offset + len(group)]
        rows = [
            [
                tr(language, "catalog_title"),
                tr(language, "catalog_time"),
                "BPM",
                tr(language, "catalog_lights"),
                tr(language, "catalog_comment"),
            ],
            *[
                [
                    song.title,
                    clock(song.duration_seconds),
                    str(song.bpm or "—"),
                    song.lights or "—",
                    song.comment or "—",
                ]
                for song in group
            ],
        ]
        # RU: Строки передаются как данные, а не HTML/Markdown. Названия и комментарии
        # не превращаются в теги, ссылки или команды. EN: Preserve literal user text.
        cells = [
            [
                {
                    "text": {
                        "type": "button",
                        "button": {
                            "text": value,
                            "style": "link",
                            "callback_data": page_buttons[row - 1][1],
                        },
                    }
                    if row and column == 0 and row <= len(page_buttons)
                    else value,
                    "align": "right" if column in (1, 2) else "left",
                    "valign": "top",
                    "is_header": row == 0,
                }
                for column, value in enumerate(values)
            ]
            for row, values in enumerate(rows)
        ]
        pages.append(
            ArticlePage(
                blocks=[
                    {"type": "paragraph", "text": heading},
                    {
                        "type": "table",
                        "cells": cells,
                        "is_bordered": True,
                        "is_striped": True,
                        "is_compact": True,
                    },
                ],
                text=heading + "\n\n" + "\n\n".join(song_card(song, language) for song in group),
                buttons=page_buttons,
            )
        )
        offset += len(group)
    return pages
