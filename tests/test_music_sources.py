import json

import pytest

from setlist_bot.domain.music_discovery import merged_tracks
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.music_sources import (
    MusicSources,
    query_target,
    songbpm_page,
    tunebat_page,
)


def card(title, seconds, bpm=None, source="Apple Music"):
    return {
        "title": title,
        "seconds": seconds,
        "bpm": bpm,
        "source": source,
        "url": "https://example.org/song",
    }


def test_merge_deduplicates_reissues_keeps_versions_and_never_guesses_bpm():
    rows = merged_tracks(
        [
            card("Ёлка", 180),
            card("елка", 181),
            card("Ёлка", 180, 100, "SongBPM"),
            card("Ёлка", 180, 101, "Tunebat"),
            card("Ёлка (Live)", 210, 120, "SongBPM"),
            card("Ёлка", 220),
            card("Без длительности", None),
        ]
    )
    assert len(rows) == 4
    studio = next(row for row in rows if row["title"] == "Ёлка (03:00)")
    assert studio["bpm"] is None and studio["bpms"] == [100, 101]
    assert next(row for row in rows if row["title"] == "Ёлка (Live)")["bpm"] == 120
    assert next(row for row in rows if row["title"] == "Без длительности")["seconds"] is None


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1/artist/123",
        "https://169.254.169.254/latest/meta-data",
        "https://open.spotify.com.attacker.example/artist/123",
        "http://musicbrainz.org/artist/test",
        "https://user:password@music.apple.com/us/artist/test/123",
        "file:///etc/passwd",
        "https://music.apple.com:443/us/artist/test/123",
        "https://music.apple.com/us/album/test/123",
    ],
)
def test_artist_urls_cannot_select_an_arbitrary_destination(value):
    with pytest.raises(AppError):
        query_target(value)


def test_artist_urls_are_reduced_to_public_identifiers_without_tracking_parameters():
    assert query_target("https://music.apple.com/us/artist/lsp/943459711?tracking=private") == (
        "apple",
        "943459711",
    )
    assert query_target("ЛСП") == ("name", "ЛСП")
    assert query_target("https://music.yandex.ru/artist/123?tracking=private") == (
        "web",
        "https://music.yandex.ru/artist/123",
    )


def test_songbpm_parser_ignores_affiliate_links_and_preserves_only_visible_metadata():
    page = """<a href="/@lsp/monetka"><p>LSP</p><p>Монетка &amp; мир</p>
    <span>Duration</span><span>3:20</span><span>BPM</span><span>128</span></a>
    <a href="/@lsp/monetka/apple-music">Apple Music</a>
    <a href="/@another/song"><p>Another</p><p>Foreign</p>Duration 3:20 BPM 999</a>
    <a href="/@lsp?after=monetka&amp;page=2">Next</a>"""
    rows, next_path = songbpm_page(page, "lsp")
    assert len(rows) == 1 and rows[0]["title"] == "Монетка & мир"
    assert rows[0]["seconds"] == 200 and rows[0]["bpm"] == 128
    assert next_path == "/@lsp?after=monetka&page=2"


def test_tunebat_parser_filters_search_recommendations_by_actual_artist_credit():
    model = {
        "searchResult": {
            "items": [
                {"id": "a", "n": "Монетка", "as": ["LSP"], "d": 200434, "b": 128},
                {"id": "b", "n": "Монетка", "as": ["Someone else"], "d": 200000, "b": 200},
            ]
        }
    }
    properties = json.dumps({"pageModel": json.dumps(model)})
    page = '<a href="/Info/-LSP/a">song</a><script>React.render(' + properties + ")</script>"
    rows = tunebat_page(page, "ЛСП")
    assert len(rows) == 1 and rows[0]["bpm"] == 128
    assert rows[0]["url"] == "https://tunebat.com/Info/-LSP/a"


async def test_bpm_source_with_same_name_needs_recording_overlap_before_import():
    class Sources(MusicSources):
        async def get(self, url):
            return '<a href="/@lsp/foreign"><p>LSP</p><p>Wrong artist</p>Duration 3:00 BPM 100</a>'

    sources = Sources(None, interval=0)
    state, outcome = await sources.step(
        {
            "phase": "songbpm",
            "artist": {"name": "ЛСП"},
            "raw": [card("Монетка", 200)],
            "warnings": [],
        },
        "ЛСП",
    )
    assert outcome is None and state["phase"] == "tunebat"
    assert len(state["raw"]) == 1 and not state["bpm_verified"]
