import re
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
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from unidecode import unidecode

from setlist_bot.application.exports import Audience
from setlist_bot.domain.rules import clock, pause_content, totals
from setlist_bot.transport.i18n import tr

FONT = "SetlistNoto"
BOLD = "SetlistNotoBold"


@lru_cache(maxsize=1)
def register_fonts() -> None:
    fonts = Path(__file__).parents[1] / "assets" / "fonts"
    pdfmetrics.registerFont(TTFont(FONT, str(fonts / "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(BOLD, str(fonts / "NotoSans-Bold.ttf")))


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
            fontName=BOLD if bold else FONT,
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
        buffer = BytesIO()
        page_size = A4 if audience == "musicians" else landscape(A4)
        document = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            leftMargin=28,
            rightMargin=28,
            topMargin=96,
            bottomMargin=36,
            pageCompression=1,
            invariant=1,
            title=f"{payload['group_name']} — {payload['concert_date']} — v{payload['version']}",
            author="Setlist Bot",
        )
        snapshot = pause_content(payload["snapshot"])
        story = (
            self._musicians(snapshot, language, transliterate)
            if audience == "musicians"
            else self._lights(snapshot, language, transliterate, document.width)
        )

        def decorate(canvas: Canvas, doc: SimpleDocTemplate) -> None:
            width, height = page_size
            canvas.saveState()
            status = tr(language, "pdf_approved" if payload["approved"] else "pdf_draft")
            canvas.setFont(BOLD, 14)
            canvas.drawRightString(width - 28, height - 30, status)
            group = self._shorten(payload["group_name"], width - 210, BOLD, 14)
            canvas.drawString(28, height - 30, group)
            canvas.setFont(FONT, 11)
            concert = f"{payload['concert_date']} · {payload['concert_title']}".rstrip(" ·")
            canvas.drawString(28, height - 49, self._shorten(concert, width - 56, FONT, 11))
            canvas.drawString(
                28,
                height - 67,
                f"{tr(language, 'pdf_' + audience)} · "
                + tr(language, "version", version=payload["version"]),
            )
            canvas.setLineWidth(0.7)
            canvas.line(28, height - 78, width - 28, height - 78)
            canvas.setFont(FONT, 10)
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

    @staticmethod
    def _block_title(block: dict[str, Any], number: int, language: str) -> str:
        if block["kind"] == "encore":
            return tr(language, "encore")
        return block["title"] or tr(language, "block_title", number=number)

    def _musicians(
        self, snapshot: dict[str, Any], language: str, transliterate: bool
    ) -> list[Flowable]:
        story: list[Flowable] = []
        blocks = snapshot["blocks"]
        multiple = len(blocks) > 1
        for number, block in enumerate(blocks, 1):
            if not block["items"]:
                continue
            if story:
                story.append(PageBreak())
            if multiple or block["title"]:
                heading = paragraph(self._block_title(block, number, language), 28, bold=True)
                heading.keepWithNext = True
                story += [heading, Spacer(1, 18)]
            for position, item in enumerate(block["items"], 1):
                prefix = f"{number}.{position}" if multiple else str(position)
                title = " ".join(title_text(item["title"], transliterate).split())
                # Keep an entire song together when it fits on one page. Extremely long
                # titles may span pages, keeping the same 52 pt size and all their words.
                story.append(
                    KeepTogether(
                        [
                            paragraph(f"{prefix}. {title}", 52, bold=True),
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
        block_times, net, total = totals(snapshot)
        columns = (32, 170, 65, 45, 215, width - 527)
        for number, block in enumerate(blocks, 1):
            heading = paragraph(self._block_title(block, number, language), 20, bold=True)
            heading.keepWithNext = True
            story += [heading, Spacer(1, 10)]
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
                prefix = f"{number}.{position}" if len(blocks) > 1 else str(position)
                title = title_text(item["title"], transliterate)
                lead_in = item.get("pause_before")
                if lead_in:
                    title = tr(language, "lead_in", time=clock(lead_in)) + "\n" + title
                rows.append(
                    [
                        paragraph(prefix, 14),
                        paragraph(title, 17, bold=True),
                        paragraph(clock(item["duration_seconds"])),
                        paragraph(str(item["bpm"]) if item.get("bpm") else ""),
                        paragraph(item.get("lights", ""), 16),
                        paragraph(item.get("comment", ""), 14),
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
            story += [
                table,
                Spacer(1, 10),
                paragraph(
                    tr(language, "block_duration", time=clock(block_times[number - 1])),
                    14,
                    bold=True,
                ),
            ]
            if number < len(blocks) and block["break_seconds"]:
                story += [
                    Spacer(1, 8),
                    paragraph(
                        tr(language, "block_break", time=clock(block["break_seconds"])),
                        16,
                        bold=True,
                    ),
                ]
            story.append(Spacer(1, 22))
        summary: list[Flowable] = [
            paragraph(tr(language, "timing_net", time=clock(net)), 17, bold=True)
        ]
        if total > net:
            summary.append(paragraph(tr(language, "timing_breaks", time=clock(total - net)), 16))
        summary += [
            paragraph(tr(language, "timing_total", time=clock(total)), 20, bold=True),
            Spacer(1, 8),
            paragraph(
                tr(language, "average_pause", time=clock(snapshot["default_pause_seconds"])), 13
            ),
        ]
        story.append(KeepTogether(summary))
        return story
