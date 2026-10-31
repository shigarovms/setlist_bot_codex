from datetime import UTC, date, datetime
from uuid import UUID

from setlist_bot.domain.calendar import CalendarEvent, render_calendar


def render(events):
    return render_calendar(
        tuple(events),
        language="ru",
        generated_at=datetime(2026, 10, 31, tzinfo=UTC),
        bot_id=1110437563,
        username="botToPract696_bot",
    )


def event(**changes):
    return CalendarEvent(
        **{
            "id": UUID(int=12),
            "date": date(2026, 12, 31),
            "summary": "Коллектив · Сольник",
            "city": "Москва",
            "description": "Начало: 20:00\nКонец: 21:40",
            "sequence": 2,
            **changes,
        }
    )


def test_all_day_event_has_exclusive_end_and_stable_uid_without_timezone():
    body = render([event()]).decode()
    assert "DTSTART;VALUE=DATE:20261231\r\nDTEND;VALUE=DATE:20270101" in body
    assert "TZID" not in body and "DTSTART:20261231T" not in body
    assert "DESCRIPTION:Начало: 20:00\\nКонец: 21:40" in body
    assert "URL:https://t.me/botToPract696_bot?start=performance_" in body
    changed = render([event(date=date(2027, 1, 3), sequence=3)]).decode()
    assert next(line for line in body.splitlines() if line.startswith("UID:")) == next(
        line for line in changed.splitlines() if line.startswith("UID:")
    )
    assert "SEQUENCE:3" in changed


def test_ics_text_cannot_inject_events_properties_or_unescaped_separators():
    body = render(
        [event(summary="Тест, &; \\\r\nEND:VEVENT\r\nBEGIN:VEVENT", description="a\rb\nc")]
    ).decode()
    unfolded = body.replace("\r\n ", "")
    assert body.splitlines().count("BEGIN:VEVENT") == 1
    assert body.splitlines().count("END:VEVENT") == 1
    assert r"SUMMARY:Тест\, &\; \\\nEND:VEVENT\nBEGIN:VEVENT" in unfolded
    assert r"DESCRIPTION:a\nb\nc" in unfolded


def test_utf8_fold_limit_is_in_octets_and_does_not_lose_cyrillic_or_emoji():
    title = "Яркая песня 🎸 " * 80
    body = render([event(summary=title)])
    assert all(len(line) <= 75 for line in body.split(b"\r\n"))
    for line in body.split(b"\r\n"):
        line.decode("utf-8")
    assert "SUMMARY:" + title in body.decode().replace("\r\n ", "")
    assert body.endswith(b"END:VCALENDAR\r\n")


def test_empty_feed_is_a_valid_calendar_and_max_date_does_not_overflow():
    assert b"BEGIN:VEVENT" not in render([])
    assert b"DURATION:P1D" in render([event(date=date.max)])
