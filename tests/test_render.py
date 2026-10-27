from datetime import date
from uuid import uuid4

import pytest

from setlist_bot.domain.entities import Concert
from setlist_bot.domain.rules import flat_content, pause_item, totals
from setlist_bot.transport.render import render_setlist


@pytest.mark.parametrize("language", ["ru", "en"])
def test_flat_render_number_padding_status_city_and_total_column(language):
    concert = Concert(group_id=uuid4(), date=date(2026, 12, 31), title="Концерт", city="Москва")
    items = [
        {"title": f"Трек {i}", "duration_seconds": 60, "pause_before": None} for i in range(10)
    ] + [pause_item(150)]
    snapshot = {"default_pause_seconds": 5, "blocks": [{"items": items, "break_seconds": 0}]}
    result = render_setlist(snapshot, concert, language, 3)
    lines = result.splitlines()
    assert "Концерт · Москва" in lines[0]
    assert ("Version 3" if language == "en" else "Версия 3") in lines[1]
    first = next(line for line in lines if line.startswith("01."))
    last = lines[-1]
    assert first.rindex("01:00") == last.rindex("13:15")
    assert "Чистое" not in result and "10." in result and "xx. " in result


def test_legacy_blocks_become_pause_rows_without_mutating_source():
    source = {
        "default_pause_seconds": 5,
        "blocks": [
            {
                "title": "A",
                "items": [{"title": "Песня", "duration_seconds": 60, "pause_before": 30}],
                "break_seconds": 900,
            },
            {
                "title": "Бис",
                "items": [{"title": "Финал", "duration_seconds": 60, "pause_before": None}],
                "break_seconds": 0,
            },
        ],
    }
    flat = flat_content(source)
    assert len(flat["blocks"]) == 1
    assert totals(flat)[2] == 1050
    assert source["blocks"][0]["items"][0]["pause_before"] == 30
