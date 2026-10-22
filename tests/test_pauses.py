from copy import deepcopy
from datetime import date

import pytest

from setlist_bot.application.groups import Groups
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import Group, PersonalDraft
from setlist_bot.domain.rules import AppError, effective_pause, totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.telegram import Processor


async def edit(service, world, operation, *args, private=False):
    _, version, _ = await service.read(world.group.id, world.concert.id, private=private)
    await service.edit(
        world.group.id, world.concert.id, version, operation, list(args), private=private
    )


def test_average_counts_only_transitions_and_keeps_zero_overrides():
    snapshot = {
        "default_pause_seconds": 5,
        "blocks": [
            {
                "break_seconds": 900,
                "items": [{"duration_seconds": 60, "pause_before": None} for _ in range(10)],
            },
            {"break_seconds": 999, "items": [{"duration_seconds": 5, "pause_before": None}]},
        ],
    }
    assert totals(snapshot) == ([645, 5], 650, 1550)
    snapshot["default_pause_seconds"] = 30
    assert totals(snapshot) == ([870, 5], 875, 1775)
    snapshot["blocks"][0]["items"][1]["pause_before"] = 0
    assert totals(snapshot) == ([840, 5], 845, 1745)


def test_explicit_lead_in_counts_before_first_and_last_song_but_average_does_not_start_block():
    snapshot = {
        "default_pause_seconds": 5,
        "blocks": [
            {
                "break_seconds": 0,
                "items": [
                    {"duration_seconds": 60, "pause_before": None},
                ],
            }
        ],
    }
    assert totals(snapshot) == ([60], 60, 60)
    snapshot["blocks"][0]["items"][0]["pause_before"] = 30
    assert totals(snapshot) == ([90], 90, 90)
    snapshot["blocks"][0]["items"].append({"duration_seconds": 60, "pause_before": 20})
    assert totals(snapshot) == ([170], 170, 170)


async def test_average_and_individual_pauses_survive_reordering_and_reload(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        for _ in range(4):
            await edit(service, world, "add", "1", str(world.song.id))
        initial, _, _ = await service.read(world.group.id, world.concert.id)
        assert initial["default_pause_seconds"] == 5
        assert totals(initial)[1] == 4 * 218 + 15
        await edit(service, world, "pause", "1", "2", "0")
        await edit(service, world, "pause", "1", "3", "60")
        await edit(service, world, "default_pause", "30")
        snapshot, _, _ = await service.read(world.group.id, world.concert.id)
        assert totals(snapshot)[1] == 4 * 218 + 90
        assert [i["pause_before"] for i in snapshot["blocks"][0]["items"]] == [None, 0, 60, None]
        # A lead-in belongs to this occurrence, including when it becomes last.
        await edit(service, world, "move", "1", "3", "4")
        last, _, _ = await service.read(world.group.id, world.concert.id)
        assert totals(last)[1] == 4 * 218 + 90
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        await edit(service, world, "move", "1", "4", "3")
        returned, _, _ = await service.read(world.group.id, world.concert.id)
        assert totals(returned)[1] == 4 * 218 + 90
        await edit(service, world, "pause", "1", "3", "default")
        reset, _, _ = await service.read(world.group.id, world.concert.id)
        assert totals(reset)[1] == 4 * 218 + 60
        await edit(service, world, "add", "1", str(world.song.id))
        appended, _, _ = await service.read(world.group.id, world.concert.id)
        assert totals(appended)[1] == 5 * 218 + 90
        await edit(service, world, "block", "Второй блок")
        await edit(service, world, "add", "2", str(world.song.id))
        await edit(service, world, "break", "1", "900")
        blocks, _, _ = await service.read(world.group.id, world.concert.id)
        assert totals(blocks) == ([5 * 218 + 90, 218], 6 * 218 + 90, 6 * 218 + 990)


async def test_group_default_is_admin_only_and_affects_only_new_concerts(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        with pytest.raises(AppError, match="admin_only"):
            await Groups(uow.store, world.member).set_default_pause(world.group.id, "30")
        with pytest.raises(AppError, match="forbidden"):
            await Groups(uow.store, world.stranger).set_default_pause(world.group.id, "30")
        await Groups(uow.store, world.admin).set_default_pause(world.group.id, "30")
        service = Setlists(uow.store, world.admin)
        new = await service.create(world.group.id, date(2027, 1, 2))
        assert (await service.read(world.group.id, new.id))[0]["default_pause_seconds"] == 30
        assert (await service.read(world.group.id, world.concert.id))[0][
            "default_pause_seconds"
        ] == 5
        assert (await uow.store.get(Group, world.other.id)).default_pause_seconds == 5
        with pytest.raises(AppError, match="not_found"):
            await service.edit(world.group.id, world.other_concert.id, 0, "default_pause", ["60"])


async def test_pauses_are_private_and_frozen_in_submissions_approvals_and_history(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        member = Setlists(uow.store, world.member)
        admin = Setlists(uow.store, world.admin)
        for _ in range(3):
            await edit(member, world, "add", "1", str(world.song.id), private=True)
        await edit(member, world, "default_pause", "30", private=True)
        await edit(member, world, "pause", "1", "2", "0", private=True)
        common, _, _ = await member.read(world.group.id, world.concert.id)
        another_draft, _, _ = await admin.read(world.group.id, world.concert.id, private=True)
        assert common["default_pause_seconds"] == another_draft["default_pause_seconds"] == 5
        submission = await member.publish(world.group.id, world.concert.id)
        await uow.store.flush()
        frozen = deepcopy(submission.snapshot)
        await edit(member, world, "default_pause", "10", private=True)
        assert submission.snapshot == frozen
        await member.apply(world.group.id, world.concert.id, submission.id, 0)
        applied, version, _ = await member.read(world.group.id, world.concert.id)
        assert applied == frozen and totals(applied)[1] == 3 * 218 + 30
        await admin.approve(world.group.id, world.concert.id, version)
        approved, version, _ = await admin.read(world.group.id, world.concert.id)
        await Groups(uow.store, world.admin).set_default_pause(world.group.id, "60")
        with pytest.raises(AppError, match="approved"):
            await member.edit(world.group.id, world.concert.id, version, "default_pause", ["0"])
        assert (await admin.read(world.group.id, world.concert.id))[0] == approved
        revision = (await admin.history(world.group.id, world.concert.id))[0]
        await admin.reopen(world.group.id, world.concert.id, version)
        await edit(member, world, "default_pause", "0")
        assert revision.snapshot == approved
        _, version, _ = await admin.read(world.group.id, world.concert.id)
        await admin.restore(world.group.id, world.concert.id, revision.id, version)
        restored, _, _ = await admin.read(world.group.id, world.concert.id)
        assert restored == approved


async def test_legacy_draft_and_revision_keep_old_timing_without_changing_json(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        legacy = {
            "blocks": [
                {
                    "title": "",
                    "break_seconds": 0,
                    "items": [
                        {"song_id": str(world.song.id), "pause_after": 0},
                        {"song_id": str(world.song.id), "pause_after": 30},
                        {"song_id": str(world.song.id), "pause_after": 0},
                    ],
                }
            ]
        }
        draft = PersonalDraft(
            group_id=world.group.id,
            concert_id=world.concert.id,
            owner_id=world.member.id,
            content=deepcopy(legacy),
        )
        uow.store.add(draft)
        await uow.store.flush()
        service = Setlists(uow.store, world.member)
        before, _, _ = await service.read(world.group.id, world.concert.id, private=True)
        assert totals(before)[1] == 3 * 218 + 30
        assert draft.content == legacy
        await edit(service, world, "default_pause", "5", private=True)
        after, _, _ = await service.read(world.group.id, world.concert.id, private=True)
        assert totals(after)[1] == 3 * 218 + 35
        submission = await service.publish(world.group.id, world.concert.id)
        await uow.store.flush()
        await service.apply(world.group.id, world.concert.id, submission.id, 0)
        historical = (await service.history(world.group.id, world.concert.id))[0]
        # Simulate an old snapshot: all pauses there were fixed numeric values.
        legacy_items = deepcopy(after["blocks"][0]["items"])
        for index, item in enumerate(legacy_items):
            item.pop("pause_before")
            item["pause_after"] = (
                effective_pause(after, after["blocks"][0]["items"][index + 1])
                if index + 1 < len(legacy_items)
                else 0
            )
        historical.snapshot = {
            "blocks": [
                {
                    "title": "",
                    "break_seconds": 0,
                    "items": legacy_items,
                }
            ]
        }
        original = deepcopy(historical.snapshot)
        await service.restore(world.group.id, world.concert.id, historical.id, 1)
        restored, _, _ = await service.read(world.group.id, world.concert.id)
        assert restored["default_pause_seconds"] == 0
        assert totals(restored) == totals(original)
        assert historical.snapshot == original


async def test_pause_commands_and_conflicts_return_current_average(engine, world):
    processor = Processor(engine)

    async def send(number, actor, command):
        return await processor.process(number, actor, -100, command, private=False)

    await send(1, 1, f"/use_concert {world.concert.id}")
    await send(2, 2, f"/use_concert {world.concert.id}")
    assert "Средняя пауза: 00:05" in (await send(3, 1, "/pause")).text
    assert "Средняя пауза: 00:30" in (await send(4, 1, "/pause 30")).text
    conflict = await send(5, 2, "/pause 10")
    assert "уже изменились" in conflict.text and "Средняя пауза: 00:30" in conflict.text
    await send(6, 1, "/add Комета")
    added = await send(7, 1, "/add Комета")
    assert "Подводка:" not in added.text and "07:46" in added.text
    custom = await send(8, 1, "/pause 2 0")
    assert "Подводка:" not in custom.text and "07:16" in custom.text
    changed = await send(9, 1, "/pause 5")
    assert "Подводка:" not in changed.text
    reset = await send(10, 1, "/pause 1 2 default")
    assert "Подводка:" not in reset.text and "07:21" in reset.text
    assert "права администратора" in (await send(11, 2, "/group_pause 30")).text
    assert "новых концертов: 00:30" in (await send(12, 1, "/group_pause 30")).text
    assert "новых концертов: 00:30" in (await send(13, 2, "/group_pause")).text
    help_text = (await send(14, 2, "/help")).text
    assert "/pause 30" in help_text and "default" in help_text


@pytest.mark.parametrize("value", ["-1", "86401", "1.5", "5 секунд", "nan", "default"])
async def test_invalid_pause_values_do_not_change_state(engine, world, value):
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        before = await service.read(world.group.id, world.concert.id)
        with pytest.raises(AppError, match="pause_input"):
            await edit(service, world, "default_pause", value)
        with pytest.raises(AppError, match="pause_input"):
            await Groups(uow.store, world.admin).set_default_pause(world.group.id, value)
        assert await service.read(world.group.id, world.concert.id) == before
