"""User-visible labels follow the authorized object, including old callbacks."""

import pytest
from test_setlist_menus import sender
from test_staffing_transport import button

from setlist_bot.domain.entities import User
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller


@pytest.mark.parametrize("language", ["ru", "en"])
async def test_group_responses_include_scope_for_empty_lists_and_song_confirmation(
    engine, world, language
):
    async with PostgresUnitOfWork(engine) as uow:
        user = await uow.store.get(User, world.admin.id)
        user.language = language
        c = Controller(uow.store, user, 1, private=True)
        await c.execute(f"use_group {world.group.id}")
        for command in (
            "members",
            "proposals",
            "reminders",
            "past_concerts",
            f"show_song {world.song.id}",
            f"archive {world.song.id}",
        ):
            response = await c.execute(command)
            assert response.text.startswith(world.group.name + "\n\n"), command
            assert response.text.count(world.group.name) == 1, command
        archived = await c.execute(f"archive {world.song.id}")
        assert (
            "Перенести песню в архив?" if language == "ru" else "Move this song to the archive?"
        ) in archived.text


async def test_team_callback_keeps_original_band_after_switching(engine, world):
    send = sender(engine)
    await send(f"use_group {world.group.id}", private=True)
    team = await send(f"sf_team {world.group.id.hex}", private=True)
    original = button(team, "Постоянная команда")
    await send(f"use_group {world.other.id}", private=True)
    response = await send(original, private=True)
    assert response.text.startswith(world.group.name + "\n\n")
    assert world.other.name not in response.text


async def test_private_invitation_redirect_does_not_disclose_band_in_other_chat(engine, world):
    from test_staffing import invite, setup_slots

    slot, _ = await setup_slots(engine, world)
    identifier = await invite(engine, world.admin, slot, world.stranger.id)
    async with PostgresUnitOfWork(engine) as uow:
        c = Controller(uow.store, world.member, -999, private=False)
        response = await c.execute(f"sf_view {identifier.hex}")
        assert world.group.name not in response.text
        assert world.concert.title not in response.text
