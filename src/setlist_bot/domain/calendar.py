"""RU: Безопасный RFC 5545. EN: All-day events, escaped text, UTF-8 octet folding."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID


@dataclass(frozen=True)
class CalendarEvent:
    id: UUID
    date: date
    summary: str
    city: str
    description: str
    sequence: int


def text_value(value: str) -> str:
    # RU: Новая строка пользователя не может создать свойство/событие календаря.
    # EN: Escape backslashes first, then separators and every newline representation.
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if c == "\n" or ord(c) >= 32)
    return value.replace("\\", "\\\\").replace("\n", r"\n").replace(";", r"\;").replace(",", r"\,")


def fold(line: str) -> str:
    """RU/EN: RFC limit is 75 OCTETS, not 75 Python characters; do not split UTF-8."""
    parts, part, size = [], "", 0
    for char in line:
        length = len(char.encode("utf-8"))
        if size + length > 75:
            parts.append(part)
            part, size = " ", 1
        part += char
        size += length
    parts.append(part)
    return "\r\n".join(parts)


def render_calendar(
    events: tuple[CalendarEvent, ...],
    *,
    language: str,
    generated_at: datetime,
    bot_id: int,
    username: str,
) -> bytes:
    name = "Выступления" if language == "ru" else "Performances"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Setlist Bot//Performances//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + name,
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    # RU/EN: DTSTAMP is the generated calendar object time, not invented historical metadata.
    stamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    for event in events:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.id.hex}@{bot_id}.setlistbot",
                "DTSTAMP:" + stamp,
                f"SEQUENCE:{event.sequence}",
                "DTSTART;VALUE=DATE:" + event.date.strftime("%Y%m%d"),
            ]
        )
        if event.date < date.max:
            lines.append("DTEND;VALUE=DATE:" + (event.date + timedelta(days=1)).strftime("%Y%m%d"))
        else:
            lines.append("DURATION:P1D")
        lines.extend(
            [
                "SUMMARY:" + text_value(event.summary),
                "LOCATION:" + text_value(event.city),
                "DESCRIPTION:" + text_value(event.description),
                f"URL:https://t.me/{username}?start=performance_{event.id.hex}",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8")
