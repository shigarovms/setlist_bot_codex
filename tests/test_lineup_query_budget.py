"""Database round-trips stay bounded as a lineup grows from one to forty roles."""

from datetime import timedelta

import pytest
from sqlalchemy import event as sql_events

from setlist_bot.domain.entities import GigInvitation, GigSlot, LineupTemplate, StaffRole, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.controller import Controller
from setlist_bot.transport.staffing_lineups import event_lineup, template_view


@pytest.mark.parametrize("screen", ["performance", "saved"])
async def test_full_lineup_uses_bounded_selects(engine, world, screen, record_property):
    async with PostgresUnitOfWork(engine) as uow:
        roles = [
            StaffRole(group_id=world.group.id, name=f"Роль {i}", name_key=f"роль {i}")
            for i in range(40)
        ]
        for role in roles:
            uow.store.add(role)
        await uow.store.flush()
        targets = []
        for concert, size in ((world.concert, 1), (world.concert2, 40)):
            if screen == "saved":
                template = LineupTemplate(
                    group_id=world.group.id,
                    name=f"Состав {size}",
                    name_key=f"состав {size}",
                    slots=[
                        {"role_id": str(role.id), "user_id": str(world.member.id)}
                        for role in roles[:size]
                    ],
                )
                uow.store.add(template)
                targets.append(template.id)
            else:
                targets.append(concert.id)
                for index, role in enumerate(roles[:size], 1):
                    slot = GigSlot(
                        group_id=world.group.id,
                        concert_id=concert.id,
                        role_id=role.id,
                        position=index,
                    )
                    uow.store.add(slot)
                    await uow.store.flush()
                    uow.store.add(
                        GigInvitation(
                            group_id=world.group.id,
                            concert_id=concert.id,
                            slot_id=slot.id,
                            user_id=world.member.id,
                            date=concert.date,
                            concert_epoch=concert.lifecycle_version,
                            expires_at=now() + timedelta(days=1),
                            status="confirmed",
                        )
                    )
    counts = []
    for target in targets:
        selects = []

        def count(_connection, _cursor, statement, _params, _context, _many, _selects=selects):
            if statement.lstrip().upper().startswith("SELECT"):
                _selects.append(statement)

        async with PostgresUnitOfWork(engine) as uow:
            controller = Controller(uow.store, world.admin, 1, private=True)
            row = await uow.store.get(LineupTemplate, target) if screen == "saved" else None
            sql_events.listen(engine.sync_engine, "before_cursor_execute", count)
            try:
                result = (
                    await template_view(controller, row)
                    if row
                    else await event_lineup(controller, target)
                )
            finally:
                sql_events.remove(engine.sync_engine, "before_cursor_execute", count)
        assert result.text
        counts.append(len(selects))
    record_property("selects_one_role", counts[0])
    record_property("selects_forty_roles", counts[1])
    assert counts[1] <= counts[0] + 1, counts
    assert counts[1] <= {"performance": 10, "saved": 6}[screen], counts
