"""RU: Ячейки Article → существующий TSV-импорт. EN: Decode data, never execute rich content."""

import csv
import io
from typing import Any

from setlist_bot.domain.rules import require

IMPORT_COMMANDS = frozenset({"add_new_songs", "song", "import"})


def rich_text(value: Any, depth: int = 0) -> str:
    require(depth <= 20, "article_format")
    if value is None:
        return ""
    if isinstance(value, str):
        require(len(value) <= 100_000, "import_size")
        return value
    if isinstance(value, list):
        require(len(value) <= 1000, "article_format")
        text = "".join(rich_text(part, depth + 1) for part in value)
        require(len(text) <= 100_000, "import_size")
        return text
    require(isinstance(value, dict), "article_format")
    if value.get("type") == "button":
        return rich_text(value.get("button", {}).get("text"), depth + 1)
    require("text" in value, "article_format")
    return rich_text(value["text"], depth + 1)


def article_heading(article: dict[str, Any]) -> str:
    """Read the first visible paragraph for recipient filtering, without fetching any URL."""
    for block in article.get("blocks", [])[:1000]:
        if block.get("type") in ("paragraph", "section_heading", "preformatted"):
            value = rich_text(block.get("text")).strip()
            if value:
                return value
        if block.get("type") == "table":
            break
    return ""


def article_table_source(article: dict[str, Any]) -> str:
    tables: list[dict[str, Any]] = []
    visited = 0

    def visit(blocks: list[dict[str, Any]], depth: int = 0) -> None:
        nonlocal visited
        require(depth <= 20, "article_format")
        for block in blocks:
            visited += 1
            require(visited <= 1000, "article_format")
            if block.get("type") == "table":
                tables.append(block)
                require(len(tables) == 1, "article_one_table")
            visit(block.get("blocks", []), depth + 1)
            for item in block.get("items", []):
                visit(item.get("blocks", []), depth + 1)

    visit(article.get("blocks", []))
    require(bool(tables), "article_table_required")
    rows = tables[0].get("cells", [])
    require(0 < len(rows) <= 501, "import_size")
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    size = 0
    for row in rows:
        require(0 < len(row) <= 50, "import_size")
        values = []
        for cell in row:
            # RU: Объединённые ячейки нельзя молча сдвинуть в соседнюю колонку.
            # EN: Reject merged layouts rather than silently corrupting column positions.
            require(
                cell.get("colspan", 1) in (None, 1) and cell.get("rowspan", 1) in (None, 1),
                "article_merged_cells",
            )
            value = rich_text(cell.get("text"))
            size += len(value)
            require(size <= 100_000, "import_size")
            values.append(value)
        writer.writerow(values)
        require(output.tell() <= 100_000, "import_size")
    return output.getvalue().rstrip("\n")
