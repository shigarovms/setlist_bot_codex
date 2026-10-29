import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from setlist_bot.domain.rules import AppError, duration, normalize, require, validate_song

FIELDS = ("title", "time", "lights", "bpm", "comment")
HEADER_NAMES = {
    "title": {"tracks", "track", "title", "song", "название", "песня", "песни"},
    "time": {"time", "duration", "длительность", "время"},
    "lights": {"lights", "light", "свет", "цвета"},
    "bpm": {"bpm", "темп"},
    "comment": {"comment", "comments", "коммент", "комментарий", "комментарии"},
}

# Do not split a color list at padding next to a comma: "белый,   красный".
SPACED_SEPARATOR = re.compile(r"(?<![,\s])[^\S\r\n]{2,}(?![,\s])")
TIME_CELL = re.compile(r"\d{1,5}:\d{2}")


def _csv_rows(source: str, delimiter: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(source), delimiter=delimiter, strict=True)
    ]


def _read_rows(source: str) -> list[list[str]]:
    if "\t" in source:
        try:
            return _csv_rows(source, "\t")
        except csv.Error as exc:
            raise AppError("import_format") from exc
    try:
        delimiter = csv.Sniffer().sniff(source, delimiters=",;").delimiter
    except csv.Error:
        delimiter = ";" if ";" in source.splitlines()[0] else ","
    csv_error = None
    try:
        csv_rows = [row for row in _csv_rows(source, delimiter) if any(row)]
    except csv.Error as exc:
        csv_error, csv_rows = exc, []
    spaced_rows = [
        SPACED_SEPARATOR.split(line.strip()) for line in source.splitlines() if line.strip()
    ]
    first_csv = {normalize(cell).lstrip("\ufeff") for cell in csv_rows[0]} if csv_rows else set()
    csv_header = bool(first_csv & HEADER_NAMES["title"] and first_csv & HEADER_NAMES["time"])
    csv_time = any(TIME_CELL.fullmatch(cell) for row in csv_rows for cell in row)
    csv_quotes = bool(
        csv_rows and re.search(r'(?:^|[,;])\s*"(?:[^"]|"")*"\s*(?=[,;]|$)', source, re.MULTILINE)
    )
    spaced_columns = any(len(row) > 1 for row in spaced_rows)
    spaced_time = any(TIME_CELL.fullmatch(cell) for row in spaced_rows for cell in row)
    if spaced_columns and (
        (spaced_time and not (csv_header or csv_quotes))
        or not (csv_header or csv_time or csv_quotes)
    ):
        return spaced_rows
    if csv_error is not None:
        raise AppError("import_format") from csv_error
    return csv_rows


@dataclass
class ImportRow:
    number: int
    title: str
    data: dict[str, Any] | None = None
    error: str = ""


@dataclass
class Table:
    rows: list[list[str]]
    headers: list[str]
    has_header: bool


@dataclass
class ParsedImport:
    table: Table
    rows: list[ImportRow] = field(default_factory=list)
    error: str = ""


def read_table(source: str, has_header: bool | None = None) -> Table:
    require(0 < len(source.strip()) <= 100_000, "import_size")
    rows = _read_rows(source)
    rows = [row for row in rows if any(row)]
    require(bool(rows) and len(rows) <= 501, "import_size")
    width = max(len(row) for row in rows)
    require(width <= 50, "import_size")
    for row in rows:
        row.extend([""] * (width - len(row)))
    while width and all(not row[width - 1] for row in rows):
        for row in rows:
            row.pop()
        width -= 1
    known = set().union(*HEADER_NAMES.values()) | {"key"}
    if has_header is None:
        has_header = any(normalize(cell).lstrip("\ufeff") in known for cell in rows[0]) and not any(
            re.fullmatch(r"\d{1,5}:\d{2}", cell) for cell in rows[0]
        )
    require(len(rows) - int(has_header) <= 500, "import_size")
    headers = rows[0] if has_header else [""] * width
    return Table(rows, headers, has_header)


def parse_import(
    source: str, mapping: dict[str, int] | None = None, has_header: bool | None = None
) -> ParsedImport:
    table = read_table(source, has_header)
    result = ParsedImport(table)
    width = len(table.rows[0])
    if mapping is None:
        if table.has_header:
            mapping = {}
            for index, header in enumerate(table.headers):
                for target, names in HEADER_NAMES.items():
                    if normalize(header).lstrip("\ufeff") in names:
                        if target in mapping:
                            result.error = "import_columns"
                            return result
                        mapping[target] = index
        else:
            if not 2 <= width <= 5:
                result.error = "import_columns"
                return result
            mapping = {target: index for index, target in enumerate(FIELDS) if index < width}
    selected = [index for index in mapping.values() if index >= 0]
    if (
        any(key not in FIELDS for key in mapping)
        or len(selected) != len(set(selected))
        or any(index >= width for index in selected)
        or any(mapping.get(key, -1) < 0 for key in ("title", "time"))
    ):
        result.error = "import_columns"
        return result
    data_rows = table.rows[1:] if table.has_header else table.rows
    if not data_rows:
        result.error = "import_empty"
    for number, cells in enumerate(data_rows, 2 if table.has_header else 1):

        def value(key: str, row_cells: list[str] = cells) -> str:
            index = mapping.get(key, -1)
            cell = row_cells[index] if index >= 0 else ""
            return "" if key in ("lights", "bpm", "comment") and cell in ("-", "—") else cell

        row = ImportRow(number, value("title"))
        result.rows.append(row)
        try:
            try:
                seconds = duration(value("time"))
            except AppError as exc:
                raise AppError("import_duration") from exc
            try:
                bpm = int(value("bpm")) if value("bpm") else None
                require(bpm is None or 0 < bpm <= 1000, "import_bpm")
            except ValueError as exc:
                raise AppError("import_bpm") from exc
            comments = [value("comment")] if value("comment") else []
            for index, cell in enumerate(cells):
                if index in selected or not cell:
                    continue
                header = table.headers[index]
                require(bool(header), "import_unnamed")
                comments.append(f"{header}: {cell}")
            data = {
                "title": value("title"),
                "duration_seconds": seconds,
                "bpm": bpm,
                "lights": value("lights"),
                "comment": "\n".join(comments),
            }
            validate_song(data)
            row.data = data
        except AppError as exc:
            row.error = exc.code
    return result
