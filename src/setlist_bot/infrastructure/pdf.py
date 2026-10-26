import re
from contextvars import ContextVar
from datetime import date
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from unidecode import unidecode

from setlist_bot.application.exports import Audience
from setlist_bot.domain.concerts import date_label
from setlist_bot.domain.display import english_colors, pause_title, position_label
from setlist_bot.domain.export_settings import settings
from setlist_bot.domain.rules import clock, flat_content, is_pause, totals
from setlist_bot.transport.i18n import tr

FONT = "SetlistNoto"
BOLD = "SetlistNotoBold"
TYPEFACES = {
    "noto": "NotoSans",
    "open": "OpenSans",
    "dejavu": "DejaVuSans",
    "liberation": "LiberationSans",
    "serif": "NotoSerif",
}
PDF_STYLE: ContextVar[dict[str, Any] | None] = ContextVar("pdf_style", default=None)


def style() -> dict[str, Any]:
    return PDF_STYLE.get() or settings({})


@lru_cache(maxsize=1)
def register_fonts() -> None:
    fonts = Path(__file__).parents[1] / "assets" / "fonts"
    pdfmetrics.registerFont(TTFont(FONT, str(fonts / "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(BOLD, str(fonts / "NotoSans-Bold.ttf")))
    for key, stem in TYPEFACES.items():
        for weight in ("Regular", "Bold"):
            pdfmetrics.registerFont(
                TTFont(f"Setlist-{key}-{weight}", str(fonts / f"{stem}-{weight}.ttf"))
            )


def title_text(title: str, transliterate: bool) -> str:
    if transliterate:
        return re.sub(r"[А-Яа-яЁё]+", lambda match: unidecode(match.group()), title)
    return title


def paragraph(text: str, size: int = 14, *, bold: bool = False) -> Paragraph:
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return Paragraph(
        escape(clean).replace("\n", "<br/>"),
        ParagraphStyle(
            "text",
            fontName=f"Setlist-{style()['font']}-{'Bold' if bold else 'Regular'}",
            alignment=1 if style()["align"] == "center" else 0,
            fontSize=size,
            leading=size * 1.2,
            spaceAfter=0,
            allowWidows=1,
            allowOrphans=1,
        ),
    )


class PdfDocuments:
    """Offline A4 layouts with embedded Cyrillic fonts and stable print sizes."""

    def __init__(self) -> None:
        register_fonts()

    def render(
        self, payload: dict[str, Any], audience: Audience, language: str, transliterate: bool
    ) -> bytes:
        config = (
            settings(payload.get("settings", {}))
            if audience == "musicians"
            else {"font": "noto", "size": 16, "align": "left"}
        )
        # RU: Экспорт работает в потоках. ContextVar не даёт настройкам одного PDF попасть в другой.
        # EN: Thread-local rendering context prevents concurrent exports leaking font settings.
        token = PDF_STYLE.set(config)
        try:
            return self._render(payload, audience, language, transliterate)
        finally:
            PDF_STYLE.reset(token)

    def _render(
        self, payload: dict[str, Any], audience: Audience, language: str, transliterate: bool
    ) -> bytes:
        buffer = BytesIO()
        page_size = A4 if audience == "musicians" else landscape(A4)
        document = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            leftMargin=28,
            rightMargin=28,
            topMargin=65,
            bottomMargin=36,
            pageCompression=1,
            invariant=1,
            title=(
                payload["group_name"]
                + " — "
                + date_label(date.fromisoformat(payload["concert_date"]), language)
                + f" — v{payload['version']}"
            ),
            author="Setlist Bot",
        )
        snapshot = flat_content(payload["snapshot"])
        story = (
            self._musicians(snapshot, language, transliterate)
            if audience == "musicians"
            else self._lights(snapshot, language, transliterate, document.width)
        )

        def decorate(canvas: Canvas, doc: SimpleDocTemplate) -> None:
            width, height = page_size
            canvas.saveState()
            status = tr(language, "pdf_approved" if payload["approved"] else "pdf_draft")
            status_width = pdfmetrics.stringWidth(status, BOLD, 10)
            info = " · ".join(
                part
                for part in (
                    payload["group_name"],
                    date_label(date.fromisoformat(payload["concert_date"]), language),
                    payload["concert_title"],
                    payload.get("city", ""),
                )
                if part
            )
            canvas.setFont(FONT, 10)
            canvas.drawString(
                28, height - 26, self._shorten(info, width - 76 - status_width, FONT, 10)
            )
            canvas.setFont(BOLD, 10)
            canvas.drawRightString(width - 28, height - 26, status)
            username = str(payload.get("bot_username", "")).lstrip("@")
            credit = tr(
                language, "pdf_created_with", bot="@" + username if username else "Telegram bot"
            )
            canvas.setFont(FONT, 7)
            canvas.drawRightString(width - 28, height - 40, credit)
            canvas.setFont(FONT, 6)
            canvas.drawRightString(
                width - 28, 18, tr(language, "pdf_page", number=canvas.getPageNumber())
            )
            canvas.restoreState()

        document.build(story, onFirstPage=decorate, onLaterPages=decorate)
        return buffer.getvalue()

    @staticmethod
    def _shorten(text: str, width: float, font: str, size: int) -> str:
        text = " ".join(text.split())
        if pdfmetrics.stringWidth(text, font, size) <= width:
            return text
        while text and pdfmetrics.stringWidth(text + "…", font, size) > width:
            text = text[:-1]
        return text + "…"

    def _musicians(
        self, snapshot: dict[str, Any], language: str, transliterate: bool
    ) -> list[Flowable]:
        story: list[Flowable] = []
        items = snapshot["blocks"][0]["items"]
        for position, item in enumerate(items, 1):
            title = " ".join(title_text(item["title"], transliterate).split())
            if is_pause(item):
                title = (
                    pause_title(language, transliterate) + "  " + clock(item["duration_seconds"])
                )
            number = position_label(position, len(items))
            story.append(
                KeepTogether(
                    [
                        paragraph(f"{number}. {title}", style()["size"], bold=True),
                        Spacer(1, 22),
                    ]
                )
            )
        return story

    def _lights(
        self, snapshot: dict[str, Any], language: str, transliterate: bool, width: float
    ) -> list[Flowable]:
        story: list[Flowable] = []
        blocks = snapshot["blocks"]
        _, _, total = totals(snapshot)
        columns = (45, 190, 80, 52, 200, width - 567)
        for block in blocks:
            rows = [
                [
                    paragraph(tr(language, "pdf_col_" + name), 13, bold=True)
                    for name in (
                        "number",
                        "title",
                        "duration",
                        "bpm",
                        "lights",
                        "comment",
                    )
                ]
            ]
            for position, item in enumerate(block["items"], 1):
                prefix = position_label(position, len(block["items"]))
                title = (
                    pause_title(language, transliterate)
                    if is_pause(item)
                    else title_text(item["title"], transliterate)
                )
                lead_in = item.get("pause_before")
                if lead_in:
                    title = tr(language, "lead_in", time=clock(lead_in)) + "\n" + title
                rows.append(
                    [
                        paragraph(prefix, 16),
                        paragraph(title, 18, bold=True),
                        paragraph(clock(item["duration_seconds"]), 16),
                        paragraph(str(item["bpm"]) if item.get("bpm") else "", 16),
                        paragraph(english_colors(item.get("lights", ""), language), 16),
                        paragraph(english_colors(item.get("comment", ""), language), 14),
                    ]
                )
            table = LongTable(rows, colWidths=columns, repeatRows=1, splitInRow=1)
            table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                    ]
                )
            )
            story += [table, Spacer(1, 22)]
        summary = LongTable(
            [
                [
                    "",
                    paragraph(tr(language, "timing_total", time="").rstrip(), 16, bold=True),
                    paragraph(clock(total), 16, bold=True),
                    "",
                    "",
                    "",
                ],
            ],
            colWidths=columns,
        )
        summary.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(summary)
        story.append(Spacer(1, 8))
        story.append(
            paragraph(
                tr(language, "average_pause", time=clock(snapshot["default_pause_seconds"])), 13
            )
        )
        return story
