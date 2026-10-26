from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4

from setlist_bot.domain.importing import parse_import
from setlist_bot.infrastructure.pdf import PdfDocuments


def sample_payload():
    source = Path(__file__).with_name("fixtures").joinpath("repertoire.tsv").read_text()
    items = [{**row.data, "pause_before": None} for row in parse_import(source).rows]
    return {
        "group_name": "Пример коллектива",
        "concert_date": "2026-12-31",
        "concert_title": "Новогодний концерт",
        "version": 7,
        "approved": False,
        "snapshot": {
            "default_pause_seconds": 5,
            "blocks": [
                {
                    "title": "",
                    "kind": "main",
                    "break_seconds": 0,
                    "items": items,
                }
            ],
        },
    }


def reader(data):
    assert data.startswith(b"%PDF-")
    return PdfReader(BytesIO(data))


def text(pdf):
    return "\n".join(page.extract_text() for page in pdf.pages)


@pytest.mark.parametrize("audience", ["musicians", "lights"])
def test_a4_cyrillic_fonts_draft_status_on_every_page_and_unchanged_payload(audience):
    payload = sample_payload()
    before = deepcopy(payload)
    pdf = reader(PdfDocuments().render(payload, audience, "ru", False))
    for page in pdf.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        assert sorted((width, height)) == pytest.approx(sorted(A4), abs=0.01)
        assert (height > width) == (audience == "musicians")
        assert "ЧЕРНОВИК" in page.extract_text()
        assert "Страница" in page.extract_text()
        font_dict = page["/Resources"]["/Font"].get_object()
        noto = [
            f.get_object()
            for f in font_dict.values()
            if "NotoSans" in str(f.get_object().get("/BaseFont"))
        ]
        assert noto and all("/FontFile2" in f["/FontDescriptor"] for f in noto)
    normalized = " ".join(text(pdf).split())
    for row in payload["snapshot"]["blocks"][0]["items"]:
        assert row["title"] in normalized
    assert payload == before


def test_musicians_keep_large_type_and_omit_all_technical_song_fields():
    payload = sample_payload()
    pdf = reader(PdfDocuments().render(payload, "musicians", "ru", False))
    assert len(pdf.pages) >= 1
    seen_sizes = []
    for page in pdf.pages:
        page.extract_text(
            visitor_text=lambda value, cm, tm, font, size: seen_sizes.append((value, size))
        )
    assert any("Славик" in value and size == 40 for value, size in seen_sizes)
    assert "03:43" not in text(pdf) and "BPM" not in text(pdf)
    assert "фиолетовый" not in text(pdf) and "коммент" not in text(pdf)
    assert "Чистое время" not in text(pdf)


def test_lights_include_fields_timing_blocks_encore_and_positive_lead_ins():
    payload = sample_payload()
    first = payload["snapshot"]["blocks"][0]
    first["break_seconds"] = 900
    first["items"][1]["pause_before"] = 30
    first["items"][0]["lights"] = "<laser> & белый"
    first["items"][0]["comment"] = "KEY: Bbmin; <выход> & актёр"
    payload["snapshot"]["blocks"].append(
        {
            "title": "Бис",
            "kind": "encore",
            "break_seconds": 0,
            "items": [deepcopy(first["items"][0])],
        }
    )
    pdf = reader(PdfDocuments().render(payload, "lights", "ru", False))
    value = " ".join(text(pdf).split())
    for expected in (
        "03:43",
        "103",
        "<laser> & белый",
        "KEY: Bbmin;",
        "<выход> & актёр",
        "15:00",
        "Итого:",
        "00:30",
    ):
        assert expected in value
    assert "Подводка: 00:05" not in value and "Подводка: 00:00" not in value
    for page in pdf.pages:
        if "Розовый" in page.extract_text() or "Комета" in page.extract_text():
            assert "BPM" in page.extract_text() and "Комментарий" in page.extract_text()


def test_approved_english_export_and_optional_transliteration_do_not_change_latin_titles():
    payload = sample_payload()
    payload["approved"] = True
    original = deepcopy(payload)
    pdf = reader(PdfDocuments().render(payload, "musicians", "en", True))
    value = " ".join(text(pdf).split())
    assert "Slavik" in value and "VIBY" in value and "Славик" not in value
    assert "DRAFT" not in value and all("APPROVED" in page.extract_text() for page in pdf.pages)
    assert payload == original


def test_long_titles_and_large_setlists_add_pages_without_shrinking_or_losing_titles():
    payload = sample_payload()
    block = payload["snapshot"]["blocks"][0]
    template = deepcopy(block["items"][0])
    block["items"] = [{**template, "title": f"Песня {n:03d}"} for n in range(1, 101)]
    block["items"][50]["title"] = "Длинная " + "Ш" * 190
    pdf = reader(PdfDocuments().render(payload, "musicians", "ru", False))
    value = "".join(text(pdf).split())
    assert len(pdf.pages) > 10
    for n in range(1, 101):
        if n != 51:
            assert f"Песня{n:03d}" in value
    assert value.count("Ш") == 190
    sizes = []
    for page in pdf.pages:
        page.extract_text(
            visitor_text=lambda value, cm, tm, font, size: sizes.append((value, size))
        )
    assert all(size == 40 for value, size in sizes if "Песня" in value or "Ш" in value)


def test_large_lighting_notes_split_across_pages_and_repeat_headers():
    payload = sample_payload()
    payload["snapshot"]["blocks"][0]["items"][0]["comment"] = (
        "Начало " + "оченьдлинныйтекст " * 110 + "КОНЕЦ"
    )
    pdf = reader(PdfDocuments().render(payload, "lights", "ru", False))
    value = text(pdf)
    assert len(pdf.pages) > 2 and "Начало" in value and "КОНЕЦ" in value
    assert sum("Комментарий" in page.extract_text() for page in pdf.pages) >= 2
