from datetime import timedelta
from io import BytesIO
from itertools import count

import pytest
from menu_helpers import export_command
from pypdf import PdfReader

from setlist_bot.application.catalog import Catalog
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.trash import Trash, TrashCleanup
from setlist_bot.domain.entities import ExportFile, ExportSnapshot, Outbox, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


def sender(engine):
    ids = count(1)

    async def send(command, *, actor=1, private=False, chat=-100):
        return await Processor(engine).process(
            next(ids),
            actor,
            actor if private else chat,
            command,
            private=private,
            display_name=f"Музыкант {actor}",
        )

    return send


def button(response, prefix):
    return next(data for _, data in response.buttons if data.startswith(prefix))


def pdf_text(file):
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(file[1])).pages)


async def test_export_submenu_all_files_back_and_default_group_language(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    shown = await send("/suggestion\nКомета\nКомета")
    menu = await send(await export_command(send, shown), actor=2)
    assert {"Для музыкантов · PDF", "Для световика · PDF", "Для всех · два PDF", "Назад"} <= dict(
        menu.buttons
    ).keys()
    assert len(menu.buttons) == 6 and "Версия 1" in menu.text and "ЧЕРНОВИК" in menu.text
    assert all(len(data.encode()) <= 64 for _, data in menu.buttons)
    files = await send(
        next(data for label, data in menu.buttons if label == "Для всех · два PDF"), actor=2
    )
    assert len(files.files) == 2
    assert all(name.endswith(".pdf") and data.startswith(b"%PDF-") for name, data in files.files)
    assert "BPM" not in pdf_text(files.files[0]) and "BPM" in pdf_text(files.files[1])
    assert all("ЧЕРНОВИК" in pdf_text(file) for file in files.files)
    back = await send(button(menu, "export_back "), actor=2)
    assert back.markup_only and "◀️ Назад" in dict(back.buttons)


async def test_export_snapshot_and_cached_files_remain_unchanged_after_edits_and_restart(
    engine, world
):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    shown = await send("/suggestion\nКомета")
    menu = await send(await export_command(send, shown))
    request = next(data for label, data in menu.buttons if label == "Для всех · два PDF")
    original = await send(request)
    await send("/add Комета")
    async with PostgresUnitOfWork(engine) as uow:
        await Catalog(uow.store, world.admin).save(
            world.group.id,
            {
                "title": "Комета новая",
                "duration_seconds": 500,
                "lights": "Изменено",
            },
            world.song.id,
            1,
        )
    # Each command is handled by a newly created Processor; the cache is durable.
    repeated = await send(request)
    assert repeated.files == original.files
    assert "Изменено" not in pdf_text(repeated.files[1])
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(ExportFile)) == 2
        snapshot = (await uow.store.find(ExportSnapshot))[0]
        assert snapshot.payload["snapshot"]["blocks"][0]["items"][0]["title"] == "Комета"


async def test_old_card_exports_its_displayed_version_even_before_first_download(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    old = await send("/suggestion\nКомета")
    await send("/add Комета")
    menu = await send(await export_command(send, old))
    file = await send(button(menu, "export_file "))
    assert pdf_text(file.files[0]).count("Комета") == 1 and "Версия 1" in pdf_text(file.files[0])
    assert len((await send("/export")).buttons) == 6


async def test_private_export_is_owner_only_and_never_publishes_draft(engine, world):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    shown = await send("/suggestion\nКомета", actor=2, private=True)
    request = await export_command(send, shown, actor=2, private=True)
    assert "устарели" in (await send(request, private=True)).text
    assert "устарели" in (await send(request)).text
    menu = await send(request, actor=2, private=True)
    file = await send(button(menu, "export_file "), actor=2, private=True)
    assert len(file.files) == 1 and "ЧЕРНОВИК" in pdf_text(file.files[0])
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Outbox)
        assert (await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id))[
            1
        ] == 0


async def test_export_checks_membership_original_chat_and_group_scope(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    shown = await send("/add Комета")
    request = await export_command(send, shown)
    assert "Нет доступа" in (await send(request, actor=3)).text
    assert "устарели" in (await send(request, chat=-200)).text
    # A valid member can use a snapshot belonging to another selected concert in the same chat.
    await send(f"/use_concert {world.concert2.id}", actor=2)
    menu = await send(request, actor=2)
    back = await send(button(menu, "export_back "), actor=2)
    assert back.markup_only and "◀️ Назад" in dict(back.buttons)


async def test_approved_and_historical_exports_keep_their_status(engine, world):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    await send("/suggestion\nКомета")
    draft = await send("/setlist")
    approval = await send("/approve")
    approved = await send(approval.buttons[0][1])
    menu = await send(await export_command(send, approved))
    file = await send(button(menu, "export_file "))
    assert "УТВЕРЖДЕНО" in pdf_text(file.files[0]) and "ЧЕРНОВИК" not in pdf_text(file.files[0])
    old_menu = await send(await export_command(send, draft))
    assert "ЧЕРНОВИК" in pdf_text((await send(button(old_menu, "export_file "))).files[0])
    history = await send("/history")
    historical = await send(button(history, "revision "))
    assert button(historical, "export_menu ")
    assert not any(data.startswith("copy_list ") for _, data in historical.buttons)


async def test_empty_export_and_language_transliteration_options(engine, world):
    send = sender(engine)
    empty = await send(f"/use_concert {world.concert.id}")
    assert not any(data.startswith("export_menu ") for _, data in empty.buttons)
    assert "пуст" in (await send("/export")).text
    shown = await send("/add Комета")
    menu = await send(await export_command(send, shown))
    english = await send(next(data for label, data in menu.buttons if label == "Язык PDF: RU"))
    latin = await send(
        next(data for label, data in english.buttons if label == "Транслитерация: выключена")
    )
    result = await send(button(latin, "export_file "))
    value = pdf_text(result.files[0])
    assert "DRAFT" in value and "Kometa" in value and "Комета" not in value


@pytest.mark.parametrize("scope", ["concert", "group"])
async def test_deleted_restored_and_expired_export_cannot_leak_cached_pdf(engine, world, scope):
    send = sender(engine)
    await send(f"/use_concert {world.concert.id}")
    shown = await send("/add Комета")
    menu = await send(await export_command(send, shown))
    request = button(menu, "export_file ")
    await send(request)
    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        if scope == "concert":
            item = await trash.delete_concert(
                world.group.id,
                world.concert.id,
                await trash.concert_fingerprint(world.group.id, world.concert.id),
            )
        else:
            item = await trash.delete_group(
                world.group.id, await trash.group_fingerprint(world.group.id)
            )
        epoch = item.lifecycle_version
    assert "удалён" in (await send(request)).text
    async with PostgresUnitOfWork(engine) as uow:
        trash = Trash(uow.store, world.admin)
        if scope == "concert":
            await trash.restore_concert(world.group.id, world.concert.id, epoch)
        else:
            await trash.restore_group(world.group.id, epoch)
    assert "истёк" in (await send(request)).text
    async with PostgresUnitOfWork(engine) as uow:
        for export in await uow.store.find(ExportSnapshot):
            export.expires_at = now() - timedelta(seconds=1)
        await TrashCleanup(uow.store).run()
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(ExportFile)
        assert not await uow.store.find(ExportSnapshot)


async def test_confirmed_publication_provides_public_export_without_exposing_later_draft(
    engine, world
):
    send = sender(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    await send("/suggestion\nКомета", actor=2, private=True)
    preview = await send("/publish", actor=2, private=True)
    await send(preview.buttons[0][1], actor=2, private=True)
    await send("/suggestion\nКомета\nКомета", actor=2, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        event = (await uow.store.find(Outbox))[0]
    menu = await send(next(data for _, data in event.buttons if data.startswith("export_menu ")))
    file = await send(button(menu, "export_file "))
    assert pdf_text(file.files[0]).count("Комета") == 1
