import importlib.util
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from test_calendar_transport import sender
from test_calendars import KEY, issue

from setlist_bot.application.calendars import Calendars
from setlist_bot.domain.calendar_vault import CalendarVault
from setlist_bot.domain.entities import CalendarSubscription
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def test_same_encrypted_link_can_be_revealed_and_legacy_saved_without_rotation(engine, world):
    identifier, token, version = await issue(engine, world.admin)
    async with PostgresUnitOfWork(engine) as uow:
        service = Calendars(uow.store, world.admin, KEY)
        row = await uow.store.get(CalendarSubscription, identifier)
        original_hash = row.token_hash
        assert token not in row.token_ciphertext and row.token_ciphertext
        assert await service.reveal(identifier) == token
        row.token_ciphertext = ""
        assert await service.reveal(identifier) is None
        await service.restore(identifier, version, token)
        assert row.token_hash == original_hash and row.version == version
        assert await service.reveal(identifier) == token
        with pytest.raises(AppError):
            await Calendars(uow.store, world.member, KEY).reveal(identifier)


async def test_changed_key_or_swapped_ciphertext_never_exposes_another_link(engine, world):
    first, first_token, _ = await issue(engine, world.admin)
    second, _, _ = await issue(engine, world.admin, world.group.id)
    async with PostgresUnitOfWork(engine) as uow:
        one = await uow.store.get(CalendarSubscription, first)
        two = await uow.store.get(CalendarSubscription, second)
        with pytest.raises(AppError, match="calendar_link_unreadable"):
            await Calendars(uow.store, world.admin, Fernet.generate_key().decode()).reveal(first)
        one.token_ciphertext = two.token_ciphertext
        with pytest.raises(AppError, match="calendar_link_unreadable"):
            await Calendars(uow.store, world.admin, KEY).reveal(first)
        # The owner can recover the same old link without disconnecting its subscribers.
        await Calendars(uow.store, world.admin, KEY).restore(first, one.version, first_token)
        assert await Calendars(uow.store, world.admin, KEY).reveal(first) == first_token


async def test_instructions_repeat_current_url_and_old_hash_only_link_is_restored_by_exact_reply(
    engine, world
):
    send = sender(engine)
    created = await send("cal_create all")
    help_action = next(action for _, action in created.buttons if action.startswith("cal_help "))
    instructions = await send(help_action)
    assert instructions.monospace_body == created.monospace_body
    assert "Apple Calendar" in instructions.text and created.monospace_body in instructions.text
    async with PostgresUnitOfWork(engine) as uow:
        row = (await uow.store.find(CalendarSubscription))[0]
        identifier, digest, version = row.id, row.token_hash, row.version
        row.token_ciphertext = ""
    old = await send(f"cal_show {identifier.hex}")
    assert "Старая ссылка продолжает работать" in old.text
    prompt = await send(f"cal_restore {identifier.hex}", remember=7000)
    assert prompt.force_reply
    assert await send(created.monospace_body, actor=2, reply=7000) is None
    invalid = await send(
        "https://wrong.example/calendar/not-your-link.ics", reply=7000, remember=7001
    )
    assert invalid.input_rejected and invalid.force_reply
    restored = await send(created.monospace_body, reply=7001)
    assert restored.monospace_body == created.monospace_body
    assert restored.delete_input and 7001 in restored.delete_message_ids
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(CalendarSubscription, identifier)
        assert row.token_hash == digest and row.version == version
        assert row.token_ciphertext
    assert (await send(f"cal_show {identifier.hex}")).monospace_body == created.monospace_body


def test_key_file_is_private_stable_and_never_generated_over_missing_encryption_key(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "calendar_key", Path(__file__).parents[1] / "scripts/calendar_key.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_key(tmp_path, lambda: False)
    path = tmp_path / ".env.calendar-key"
    contents = path.read_bytes()
    CalendarVault(contents.decode().strip().partition("=")[2])
    assert path.stat().st_mode & 0o777 == 0o600
    module.ensure_key(tmp_path, lambda: True)
    assert path.read_bytes() == contents
    path.unlink()
    with pytest.raises(RuntimeError, match="key is missing"):
        module.ensure_key(tmp_path, lambda: True)
    assert not path.exists()
