"""RU: Табличный каталог Telegram. EN: Pure, bounded Article rendering without API calls."""

from dataclasses import dataclass
from typing import Any

from setlist_bot.domain.entities import Song
from setlist_bot.domain.rules import clock
from setlist_bot.transport.i18n import tr
from setlist_bot.transport.render import song_card


@dataclass
class ArticlePage:
    blocks: list[dict[str, Any]]
    text: str
    buttons: list[tuple[str, str]]


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
