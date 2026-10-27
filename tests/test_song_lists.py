import asyncio
import re
from copy import deepcopy
from itertools import count
from pathlib import Path

import pytest
from menu_helpers import options, variants

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Outbox, Revision
from setlist_bot.domain.importing import parse_import
from setlist_bot.domain.rules import AppError, totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor

TITLES = ["Славик", "Сердце лёд", "Меланхолия", "Подожгу", "Атомы", "VIBY"]
SONG_LIST = "\n".join(TITLES)


async def repertoire(engine, world):
    source = Path(__file__).with_name("fixtures").joinpath("repertoire.tsv").read_text()
    async with PostgresUnitOfWork(engine) as uow:
        for row in parse_import(source).rows:
            if row.title != "Комета":
                await Catalog(uow.store, world.admin).save(world.group.id, row.data)


def messenger(engine):
    processor, numbers = Processor(engine), count(1)

    async def send(command, *, actor=1, private=False):
        return await processor.process(
            next(numbers),
            actor,
            actor if private else -100,
            command,
            private=private,
            display_name="Алексей" if actor == 1 else "Борис",
        )

    return send


def song_titles(snapshot):
    return [i["title"] for b in snapshot["blocks"] for i in b["items"]]


async def test_exact_user_add_list_is_atomic_ordered_and_single_numbered(engine, world):
    await repertoire(engine, world)
    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}")
    response = await send("/add\n" + SONG_LIST)
    displayed = re.findall(r"^\d+\. (.+?)  +\d+:\d{2}$", response.text, re.MULTILINE)
    assert displayed == TITLES
    assert "1.1." not in response.text and "Блок 1" not in response.text
    assert "+ 00:00" not in response.text and "Подводка: 00:00" not in response.text
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        snapshot, version, _ = await service.read(world.group.id, world.concert.id)
        assert version == 1 and song_titles(snapshot) == TITLES
        assert totals(snapshot)[1] == 226 + 216 + 246 + 247 + 248 + 230 + 25
        assert [r.action for r in await service.history(world.group.id, world.concert.id)] == [
            "add_list",
            "created",
        ]
    zero = await send("/pause 0")
    assert "Подводка:" not in zero.text
    before = await send("/add\n30 сек")
    assert "пауза" in before.text and "00:30" in before.text


async def test_suggestions_replace_whole_setlist_and_number_each_proposer(engine, world):
    await repertoire(engine, world)
    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}")
    first = await send("/suggestion\n" + SONG_LIST)
    first_variants = await variants(send, first)
    first_button = next(
        data for label, data in first_variants.buttons if label == "Алексей · вариант 1"
    )
    await send("/add Комета")
    await send(f"/use_concert {world.concert.id}", actor=2)
    second = await send("/suggestion\nVIBY\nСлавик", actor=2)
    assert "гитарист · вариант 1" in dict((await variants(send, second, actor=2)).buttons)
    stale = await send("/suggestion\nАтомы")
    assert "уже изменились" in stale.text
    third = await send("/suggestion\nАтомы\nСердце лёд")
    assert "Алексей · вариант 2" in dict((await variants(send, third)).buttons)
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.admin)
        current, version, _ = await service.read(world.group.id, world.concert.id)
        assert version == 4 and song_titles(current) == ["Атомы", "Сердце лёд"]
        revisions = await service.history(world.group.id, world.concert.id)
        old = next(
            r for r in revisions if r.proposer_id == world.admin.id and r.suggestion_number == 1
        )
        assert song_titles(old.snapshot) == TITLES
    preview = await send(first_button)
    assert "6. VIBY" in preview.text
    restore = await send(preview.buttons[0][1])
    restored = await send(restore.buttons[0][1])
    assert "6. VIBY" in restored.text
    history = await send("/versions")
    assert {"Алексей · вариант 1", "Алексей · вариант 2", "гитарист · вариант 1"} <= {
        label for label, _ in history.buttons
    }


async def test_private_suggestion_stays_private_until_explicit_publish_and_apply(engine, world):
    await repertoire(engine, world)
    send = messenger(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    await send("/add\nКомета\nРадуга", actor=2, private=True)
    own = await send("/suggestion\n" + SONG_LIST, actor=2, private=True)
    assert "Личный черновик" in own.text and "6. VIBY" in own.text
    async with PostgresUnitOfWork(engine) as uow:
        common, version, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert version == 0 and not song_titles(common)
        assert not await uow.store.find(Outbox)
        assert (
            len(await uow.store.find(Revision, group_id=world.group.id, suggestion_number=1)) == 0
        )
    await send("/publish", actor=2, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox))[0]
    result = await send(event.buttons[0][1])
    assert "гитарист · вариант 1" in dict((await variants(send, result)).buttons)
    async with PostgresUnitOfWork(engine) as uow:
        revision = (
            await Setlists(uow.store, world.admin).history(world.group.id, world.concert.id)
        )[0]
        assert revision.author_id == world.admin.id and revision.proposer_id == world.member.id
        assert song_titles(revision.snapshot) == TITLES


@pytest.mark.parametrize("command", ["/add", "/suggestion"])
async def test_invalid_or_ambiguous_line_leaves_entire_setlist_untouched(engine, world, command):
    await repertoire(engine, world)
    send = messenger(engine)
    await send(f"/use_concert {world.concert.id}")
    await send("/add Комета")
    async with PostgresUnitOfWork(engine) as uow:
        before = await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
        await Catalog(uow.store, world.admin).save(
            world.group.id, {"title": "VIBY", "duration_seconds": 50}
        )
    response = await send(command + "\nСлавик\nXXXXXXXXZZZZZZZ\nVIBY")
    assert "Список не применён" in response.text
    assert "Строка 2" in response.text and "не найдена" in response.text
    assert "Строка 3" in response.text and "несколько совпадений" in response.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (
            await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id) == before
        )


async def test_batch_alias_normalization_limits_rbac_and_approval(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        catalog = Catalog(uow.store, world.admin)
        await catalog.alias(world.group.id, world.song.id, "вечеринки")
        service = Setlists(uow.store, world.member)
        await service.edit_songs(
            world.group.id, world.concert.id, 0, [" KOMETA ", "ВЕЧЕРИНКИ"], replace=True
        )
        before = await service.read(world.group.id, world.concert.id)
        assert song_titles(before[0]) == ["Комета", "Комета"]
        for titles in ([], ["Комета"] * 201):
            with pytest.raises(AppError, match="song_list_input"):
                await service.edit_songs(world.group.id, world.concert.id, 1, titles, replace=True)
        with pytest.raises(AppError, match="forbidden"):
            await Setlists(uow.store, world.stranger).edit_songs(
                world.group.id, world.concert.id, 1, ["Комета"], replace=True
            )
        with pytest.raises(AppError, match="not_found"):
            await service.edit_songs(
                world.group.id, world.other_concert.id, 1, ["Комета"], replace=True
            )
        with pytest.raises(AppError, match="song_list_errors"):
            await service.edit_songs(
                world.group.id, world.concert.id, 1, ["Чужая песня"], replace=True
            )
        assert await service.read(world.group.id, world.concert.id) == before
        await Setlists(uow.store, world.admin).approve(world.group.id, world.concert.id, 1)
        with pytest.raises(AppError, match="approved"):
            await service.edit_songs(world.group.id, world.concert.id, 2, ["Комета"], replace=True)
        approved = deepcopy((await service.read(world.group.id, world.concert.id))[0])
        await service.edit_songs(
            world.group.id, world.concert.id, 0, ["Комета"], replace=True, private=True
        )
        assert (await service.read(world.group.id, world.concert.id))[0] == approved


async def test_concurrent_batch_replacements_produce_one_version_and_one_conflict(engine, world):
    async def replace(user):
        try:
            async with PostgresUnitOfWork(engine) as uow:
                await Setlists(uow.store, user).edit_songs(
                    world.group.id, world.concert.id, 0, ["Комета"], replace=True
                )
            return "ok"
        except AppError as exc:
            return exc.code

    assert sorted(await asyncio.gather(replace(world.admin), replace(world.member))) == [
        "conflict",
        "ok",
    ]
    processor = Processor(engine)
    await processor.process(1, 1, -100, f"/use_concert {world.concert.id}", private=False)
    result = await processor.process(2, 1, -100, "/add\nКомета\nКомета", private=False)
    assert "Версия 2" in result.text
    assert await processor.process(2, 1, -100, "/add\nКомета\nКомета", private=False) is None
    async with PostgresUnitOfWork(engine) as uow:
        current, version, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert version == 2 and song_titles(current) == ["Комета"] * 3


async def test_removed_encore_not_offered_in_options(engine, world):
    send = messenger(engine)
    card = await send(f"/use_concert {world.concert.id}")
    menu = await options(send, card)
    assert all("бис" not in label.lower() for label, _ in menu.buttons)
