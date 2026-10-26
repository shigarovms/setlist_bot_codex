import string

import pytest

from setlist_bot.domain.rules import AppError, duration, normalize, search_key, totals
from setlist_bot.transport.i18n import MESSAGES
from setlist_bot.transport.telegram import chunks


@pytest.mark.parametrize(("value", "expected"), [("00:05", 5), ("03:38", 218), ("105:00", 6300)])
def test_duration(value, expected):
    assert duration(value) == expected


@pytest.mark.parametrize("value", ["00:00", "-1:30", "03:60", "3.50", "", "a:01"])
def test_invalid_duration(value):
    with pytest.raises(AppError, match="duration"):
        duration(value)


def test_search_normalization():
    assert normalize("  Ёлка   МОЯ ") == "елка моя"
    assert search_key("Комета") == search_key("Kometa")


def test_time_includes_only_internal_pauses_and_breaks():
    snapshot = {
        "blocks": [
            {"break_seconds": 900, "items": [{"duration_seconds": 2700, "pause_after": 10}]},
            {"break_seconds": 999, "items": [{"duration_seconds": 2700, "pause_after": 20}]},
        ]
    }
    assert totals(snapshot) == ([6300], 6300, 6300)
    snapshot["blocks"][0]["items"].append({"duration_seconds": 5, "pause_after": 50})
    assert totals(snapshot) == ([6315], 6315, 6315)


def test_localization_keys_and_placeholders_match():
    assert MESSAGES["ru"].keys() == MESSAGES["en"].keys()
    formatter = string.Formatter()
    for key, value in MESSAGES["ru"].items():

        def fields(text):
            return {f for _, f, _, _ in formatter.parse(text) if f is not None}

        assert fields(value) == fields(MESSAGES["en"][key]), key


def test_telegram_splitting_counts_utf16():
    text = "🎸" * 4000 + "конец"
    result = chunks(text)
    assert "".join(result) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in result)
