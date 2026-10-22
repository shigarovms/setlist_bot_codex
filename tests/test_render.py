from datetime import date
from uuid import uuid4

import pytest

from setlist_bot.domain.entities import Concert
from setlist_bot.domain.rules import totals
from setlist_bot.transport.render import render_setlist


@pytest.mark.parametrize("language", ["ru", "en"])
def test_render_hides_inherited_lead_ins_and_zero_breaks_but_counts_all_time(language):
    concert = Concert(group_id=uuid4(), date=date(2026, 12, 31))
    snapshot = {
        "default_pause_seconds": 5,
        "blocks": [
            {
                "title": "",
                "break_seconds": 0,
                "items": [
                    {"title": "Розовый фламинго", "duration_seconds": 223, "pause_before": None},
                    {"title": "Комета", "duration_seconds": 218, "pause_before": None},
                    {"title": "Атомы", "duration_seconds": 248, "pause_before": 5},
                    {"title": "VIBY", "duration_seconds": 230, "pause_before": 0},
                ],
            }
        ],
    }
    result = render_setlist(snapshot, concert, language, 1)
    lead_in = "Подводка:" if language == "ru" else "Lead-in:"
    assert result.count(lead_in) == 1  # An explicit 5s is visible; inherited 5s is hidden.
    assert lead_in + " 00:05\n3. Атомы" in result
    assert "Перерывы:" not in result and "Breaks:" not in result
    assert "00:00" not in result and "15:29" in result
    assert totals(snapshot)[1] == 929
    track_lines = [line for line in result.splitlines() if line[:1].isdigit() and ". " in line]
    assert len({line.rfind("  ") for line in track_lines}) == 1
    assert "─" in result and "1.1." not in result


def test_render_keeps_blocks_encore_positive_intermission_and_long_titles():
    concert = Concert(group_id=uuid4(), date=date(2026, 12, 31))
    title = "Длинное название песни со всеми словами и символами <>&"
    snapshot = {
        "default_pause_seconds": 5,
        "blocks": [
            {
                "title": "",
                "break_seconds": 900,
                "items": [
                    {"title": title, "duration_seconds": 60, "pause_before": 30},
                ],
            },
            {
                "title": "Бис",
                "break_seconds": 0,
                "items": [
                    {"title": "Комета", "duration_seconds": 218, "pause_before": None},
                ],
            },
        ],
    }
    result = render_setlist(snapshot, concert, "ru", 2)
    assert "Подводка: 00:30\n1.1." in result
    assert "Бис\n2.1. Комета" in result
    assert "Перерывы: 15:00" in result and "Итого: 20:08" in result
    assert "Длительность блока: 01:30" in result
    assert "<>&" in result
    assert all(word in result for word in title.split())
