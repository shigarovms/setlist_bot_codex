from datetime import timedelta
from pathlib import Path

import pytest

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.importing import Importer
from setlist_bot.domain.entities import CatalogImport, now
from setlist_bot.domain.importing import parse_import
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor

REPERTOIRE = Path(__file__).with_name("fixtures").joinpath("repertoire.tsv").read_text()
SPACED_REPERTOIRE = (
    Path(__file__).with_name("fixtures").joinpath("repertoire-spaces.txt").read_text()
)


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize(
    "row,is_table",
    [
        ("Тестовая песня  03:20", True),
        ("Тестовая песня\t03:20", True),
        ("Тестовая песня  03:20", True),
    ],
)
async def test_single_row_after_song_command_uses_table_or_legacy_pipe_format(
    engine, world, private, row, is_table
):
    from datetime import UTC, datetime

    from aiogram import Bot, Dispatcher
    from aiogram.types import Chat, Message, MessageEntity, Update, User
    from test_telegram import FakeTelegram

    from setlist_bot.transport.telegram import make_router

    processor = Processor(engine)
    if private:
        await processor.process(1, 1, 1, f"/use_group {world.group.id}", private=True)
    session = FakeTelegram()
    async with Bot("1110437563:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        dispatcher = Dispatcher()
        dispatcher.include_router(make_router(processor, "botToPract696_bot"))
        command = "/song@botToPract696_bot"
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=2,
                message=Message(
                    message_id=100,
                    date=datetime.now(UTC),
                    from_user=User(id=1, is_bot=False, first_name="Админ"),
                    chat=Chat(
                        id=1 if private else -100, type="private" if private else "supergroup"
                    ),
                    text=command + "\n" + row,
                    entities=[MessageEntity(type="bot_command", offset=0, length=len(command))],
                ),
            ),
        )
    assert session.admin_requests == 0
    assert any("новых — 1" in method.text for method in session.sent) == is_table
    assert any("Проверьте и подтвердите" in method.text for method in session.sent)
    assert not any("Проверьте поля песни" in method.text for method in session.sent)
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(CatalogImport)) == int(is_table)


def test_user_spaced_paste_matches_original_tsv_for_all_ten_songs():
    parsed = parse_import(SPACED_REPERTOIRE)
    assert not parsed.error and len(parsed.rows) == 10
    assert not any(row.error for row in parsed.rows)
    assert [row.data for row in parsed.rows] == [row.data for row in parse_import(REPERTOIRE).rows]


def test_spaced_columns_keep_padding_in_comma_separated_lights():
    parsed = parse_import("Группа крови  03:38  белый,   красный  110  выход актёра, лазеры")
    assert not parsed.rows[0].error
    assert parsed.rows[0].data == {
        "title": "Группа крови",
        "duration_seconds": 218,
        "lights": "белый,   красный",
        "bpm": 110,
        "comment": "выход актёра, лазеры",
    }


def test_csv_with_time_and_spaces_inside_quoted_title_stays_csv():
    parsed = parse_import('"Песня  03:38  ремикс",04:00,"белый,  красный",110,коммент')
    assert not parsed.rows[0].error
    assert parsed.rows[0].data["title"] == "Песня  03:38  ремикс"
    assert parsed.rows[0].data["duration_seconds"] == 240
    assert parsed.rows[0].data["lights"] == "белый,  красный"


def test_spaced_headers_and_nonbreaking_column_spaces():
    source = (
        "TRACKS  TIME  LIGHTS  BPM  KEY\nИнтро  00:05\nКомета  03:38  белый, красный  110  Bbmin"
    )
    parsed = parse_import(source.replace("  ", "\u00a0\u00a0"))
    assert not parsed.error and not any(row.error for row in parsed.rows)
    assert parsed.rows[0].data["duration_seconds"] == 5
    assert parsed.rows[1].data["comment"] == "KEY: Bbmin"


@pytest.mark.parametrize("time", ["00:00", "03:99", "ошибка"])
def test_invalid_spaced_duration_keeps_correct_song_title(time):
    parsed = parse_import(f"Розовый фламинго  {time}  розовый, белый  103")
    assert parsed.rows[0].title == "Розовый фламинго"
    assert parsed.rows[0].error == "import_duration"


def test_user_provided_ten_rows_parse_without_headers():
    parsed = parse_import(REPERTOIRE)
    assert not parsed.error and len(parsed.rows) == 10
    assert not any(row.error for row in parsed.rows)
    songs = [row.data for row in parsed.rows]
    assert sum(song["duration_seconds"] for song in songs) == 2323
    assert songs[0] == {
        "title": "Розовый фламинго",
        "duration_seconds": 223,
        "lights": "розовый, белый, красный",
        "bpm": 103,
        "comment": "",
    }
    assert songs[7]["comment"] == "коммент" and songs[7]["bpm"] == 128
    assert songs[8]["title"] == "VIBY"


def test_headers_optional_cells_key_and_unnamed_empty_column():
    parsed = parse_import(
        "TRACKS\tTIME\tLIGHTS\tBPM\tKEY\t\n"
        "Интро\t00:05\t\t\t\t\n"
        "Интро Субботник 26\t01:30\t\t128\tBbmin\t"
    )
    assert not parsed.error and not any(r.error for r in parsed.rows)
    assert parsed.rows[0].data["bpm"] is None
    assert parsed.rows[0].data["duration_seconds"] == 5
    assert parsed.rows[1].data["comment"] == "KEY: Bbmin"
    assert "key" not in parsed.rows[1].data


def test_reordered_headers_and_extra_named_columns():
    parsed = parse_import(
        "BPM\tKEY\tTIME\tTRACKS\tLIGHTS\tSTAGE\n110\tBbmin\t03:38\tКомета\tкрасный\tвыход актёра"
    )
    assert not parsed.rows[0].error
    assert parsed.rows[0].data == {
        "title": "Комета",
        "duration_seconds": 218,
        "lights": "красный",
        "bpm": 110,
        "comment": "KEY: Bbmin\nSTAGE: выход актёра",
    }


def test_csv_quoting_keeps_lights_together():
    parsed = parse_import(
        'TRACKS,TIME,LIGHTS,BPM,KEY\n"Название, с запятой",03:38,"белый, красный",,Bbmin'
    )
    assert not parsed.error and not parsed.rows[0].error
    assert parsed.rows[0].data["lights"] == "белый, красный"
    assert parsed.rows[0].data["bpm"] is None


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ("Песня\t00:00", "import_duration"),
        ("Песня\t03:99", "import_duration"),
        ("Песня\t03:38\t\tбыстро", "import_bpm"),
        ("Песня\t03:38\t\t-1", "import_bpm"),
        ("\t03:38", "song_input"),
    ],
)
def test_invalid_rows_are_explained(row, error):
    assert parse_import(row).rows[0].error == error


def test_song_called_time_is_not_a_header():
    parsed = parse_import("Time\t03:38\tбелый\t110")
    assert not parsed.table.has_header and parsed.rows[0].data["title"] == "Time"


def test_oversized_table_is_rejected():
    with pytest.raises(AppError, match="import_size"):
        parse_import("Песня\t00:05\n" * 501)


@pytest.mark.parametrize("source", [REPERTOIRE, SPACED_REPERTOIRE], ids=["tabs", "spaces"])
async def test_import_preview_confirmation_duplicates_and_reimport(engine, world, source):
    processor = Processor(engine)
    preview = await processor.process(100, 1, -100, "/song\n" + source, private=False)
    assert "новых — 9" in preview.text and "повторяющихся — 1" in preview.text
    assert "ошибочных строк — 0" in preview.text and "коммент" in preview.text
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await Catalog(uow.store, world.admin).list_songs(world.group.id)) == 1
    result = await processor.process(101, 1, -100, preview.buttons[0][1], private=False)
    assert "Добавлено песен: 9" in result.text
    async with PostgresUnitOfWork(engine) as uow:
        catalog = Catalog(uow.store, world.admin)
        songs = await catalog.list_songs(world.group.id)
        assert len(songs) == 10
        assert (await catalog.song(world.group.id, world.song.id)).comment == "KEY: Bbmin"
        assert next(s for s in songs if s.title == "Атомы").comment == "коммент"
        assert len(await catalog.list_songs(world.other.id)) == 1
    repeated = await processor.process(102, 1, -100, "/song\n" + source, private=False)
    assert "новых — 0" in repeated.text and "повторяющихся — 10" in repeated.text
    assert not any(data.startswith("confirm ") for _, data in repeated.buttons)


async def test_import_is_admin_only_and_errors_block_entire_batch(engine, world):
    processor = Processor(engine)
    denied = await processor.process(100, 2, -100, "/song\n" + REPERTOIRE, private=False)
    assert "администратора" in denied.text
    invalid = await processor.process(
        101, 1, -100, "/song\nХорошая\t00:05\nПлохая\t00:00", private=False
    )
    assert "ошибочных строк — 1" in invalid.text
    assert not any(data.startswith("confirm ") for _, data in invalid.buttons)
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await Catalog(uow.store, world.admin).list_songs(world.group.id)) == 1


async def test_changed_catalog_invalidates_import_confirmation(engine, world):
    processor = Processor(engine)
    preview = await processor.process(100, 1, -100, "/song\nНовая\t00:05", private=False)
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "Новая", "duration_seconds": 10}
        )
    result = await processor.process(101, 1, -100, preview.buttons[0][1], private=False)
    assert "Каталог или настройки импорта изменились" in result.text
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await Catalog(uow.store, world.admin).list_songs(world.group.id)) == 2


async def test_manual_column_mapping_persists_between_commands(engine, world):
    processor = Processor(engine)
    preview = await processor.process(100, 1, -100, "/song\n03:43\tНовая", private=False)
    start = next(data for _, data in preview.buttons if data.startswith("map_start "))
    headers = await processor.process(101, 1, -100, start, private=False)
    step = await processor.process(102, 1, -100, headers.buttons[1][1], private=False)
    # Title is column 2; time is column 1; optional columns are absent.
    for update, choice in enumerate((1, 0, -1, -1, -1), 103):
        callback = next(data for _, data in step.buttons if data.endswith(f" {choice}"))
        step = await processor.process(update, 1, -100, callback, private=False)
    assert "новых — 1" in step.text and "ошибочных строк — 0" in step.text
    result = await processor.process(108, 1, -100, step.buttons[0][1], private=False)
    assert "Добавлено песен: 1" in result.text


async def test_import_scope_expiry_and_batch_duplicate_conflicts(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        importer = Importer(uow.store, world.admin, -100)
        job = await importer.create(world.group.id, "Новая\t00:05\nНовая\t00:10")
        preview = await importer.preview(world.group.id, job.id)
        assert len(preview.errors) == 1 and preview.errors[0].error == "import_duplicate_conflict"
        with pytest.raises(AppError, match="import_fix"):
            await importer.commit(world.group.id, job.id, preview.fingerprint)
        with pytest.raises(AppError, match="confirmation"):
            await importer.preview(world.other.id, job.id)
        with pytest.raises(AppError, match="confirmation"):
            await Importer(uow.store, world.admin, 1).preview(world.group.id, job.id)
        job.expires_at = now() - timedelta(seconds=1)
        with pytest.raises(AppError, match="confirmation"):
            await importer.preview(world.group.id, job.id)


async def test_cancelled_import_cannot_be_reopened_by_old_button(engine, world):
    processor = Processor(engine)
    preview = await processor.process(100, 1, -100, "/song\nНовая\t00:05", private=False)
    mapping = next(data for _, data in preview.buttons if data.startswith("map_start "))
    await processor.process(101, 1, -100, "/cancel", private=False)
    response = await processor.process(102, 1, -100, mapping, private=False)
    assert "уже выполнено" in response.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(CatalogImport))[0].status == "cancelled"
