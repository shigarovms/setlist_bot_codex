from datetime import date
from uuid import UUID, uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import GigInvitation, GigSlot, Membership, StaffContact, User, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def action(client, engine, session, operation, *, succeeds=True, **fields):
    body = {**session, "action": operation, "request_id": str(uuid4()), **fields}
    queued = await post(client, 1, "staff_action", body)
    assert queued["status"] == "pending"
    assert await deliver_request(engine, queued["request"]) == succeeds
    receipt = await get(client, 1, "catalog_status", **session, request=queued["request"])
    assert await post(client, 1, "staff_action", body) == receipt
    return receipt


async def test_directory_add_search_preference_and_isolation(engine, world):
    await setup_workspace(engine, world)
    band = str(world.group.id)
    async with PostgresUnitOfWork(engine) as uow:
        other = Staffing(uow.store, world.admin)
        role = await other.role(world.other.id, "Вокал")
        foreign_contact = await other.contact(world.other.id, role.id, world.stranger.id)
        person = await uow.store.get(User, world.stranger.id)
        person.profile = {"name": "Гость", "travel": True, "private_extra": "hidden"}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": band})
        member = await post(client, 2, "open_repertoire", {"band": band})
        await get(client, 2, "staff_directory", 403, band=band, section="contacts")
        await get(
            client,
            1,
            "staff_directory",
            403,
            band=band,
            section="contacts",
            record=str(foreign_contact.id),
        )
        await post(client, 2, "staff_action", {**member, "action": "contact_add"}, 403)
        person = await post(client, 1, "team_recipient", {"band": band, "reference": "3"})
        added = await action(
            client, engine, admin, "contact_add", role="Вокал 2", reference="3", person=person["id"]
        )
        assert not added["permanent"]
        rows = (
            await get(client, 1, "staff_directory", band=band, section="contacts", query="гос")
        )["rows"]
        assert len(rows) == 1 and rows[0]["role"] == "Вокал 2"
        assert "profile" not in rows[0]
        record = {"band": band, "section": "contacts", "record": added["contact"]}
        card = (await get(client, 1, "staff_directory", **record))["contact"]
        assert card["profile"] == {"travel": True}
        args = {"target": added["contact"], "version": card["version"]}
        await action(client, engine, admin, "contact_preference", preference="hidden", **args)
        stale = await action(
            client,
            engine,
            admin,
            "contact_preference",
            succeeds=False,
            preference="preferred",
            **args,
        )
        assert stale["error"] == "input_expired"
        assert (await get(client, 1, "staff_directory", **record))["contact"][
            "preference"
        ] == "hidden"
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(
                Membership, group_id=world.group.id, user_id=world.stranger.id
            )
            row = await uow.store.get(StaffContact, UUID(added["contact"]))
            assert not row.permanent
            assert (await uow.store.get(StaffContact, foreign_contact.id)).preference == "normal"


async def test_lineup_preview_copy_edit_delete_keep_existing_performances(engine, world):
    await setup_workspace(engine, world)
    band = str(world.group.id)
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Вокал")
        await staff.set_permanent(world.group.id, role.id, world.member.id)
        foreign_role = await staff.role(world.other.id, "Бас")
        foreign = await staff.template(world.other.id, "Чужой", [{"role_id": str(foreign_role.id)}])
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": band})
        await get(
            client, 1, "staff_directory", 403, band=band, section="lineups", record=str(foreign.id)
        )
        await post(
            client,
            2,
            "staff_lineup_preview",
            {"band": band, "name": "Коллектив", "quick": "Вокал"},
            403,
        )
        plan = await post(
            client,
            1,
            "staff_lineup_preview",
            {"band": band, "name": "Дуэт", "quick": "Вокал\nВокал: 3"},
        )
        assert plan["rows"][1]["person"] == str(world.stranger.id)
        assert (await get(client, 1, "staff_directory", band=band, section="lineups"))["rows"] == []
        made = await action(client, engine, admin, "lineup_create", **plan)
        key = {"band": band, "section": "lineups", "record": made["lineup"]}
        card = (await get(client, 1, "staff_directory", **key))["lineup"]
        assert len(card["rows"]) == 2
        assert card["rows"][0]["suggested"] == "2"
        assert card["rows"][1]["suggested"] == card["rows"][1]["name"] == "3"
        await action(client, engine, admin, "lineup_create", succeeds=False, **plan)
        args = {"target": card["id"], "version": card["version"]}
        clone = await action(client, engine, admin, "lineup_copy", **args, name="Копия")
        assert clone["lineup"] != card["id"]

        from setlist_bot.application.setlists import Setlists

        async with PostgresUnitOfWork(engine) as uow:
            event = await Setlists(uow.store, world.admin).create(world.group.id, date(2099, 5, 6))
            await Staffing(uow.store, world.admin).apply_template(
                event.id, UUID(card["id"]), card["version"]
            )
        edited = await post(
            client,
            1,
            "staff_lineup_preview",
            {"band": band, "name": "Трио", "rows": [{"role": "Гитара"}, *plan["rows"]]},
        )
        await action(client, engine, admin, "lineup_update", **args, **edited)
        stale = await action(client, engine, admin, "lineup_delete", succeeds=False, **args)
        assert stale["error"] == "input_expired"
        fresh = (await get(client, 1, "staff_directory", **key))["lineup"]
        assert fresh["name"] == "Трио" and [row["role"] for row in fresh["rows"]] == [
            "Гитара",
            "Вокал",
            "Вокал",
        ]
        await action(
            client, engine, admin, "lineup_delete", target=fresh["id"], version=fresh["version"]
        )
        assert [
            row["name"]
            for row in (await get(client, 1, "staff_directory", band=band, section="lineups"))[
                "rows"
            ]
        ] == ["Копия"]
        async with PostgresUnitOfWork(engine) as uow:
            slots = await uow.store.find(GigSlot, concert_id=event.id, removed=False)
            assert {slot.preferred_user_id for slot in slots} == {
                world.member.id,
                world.stranger.id,
            }
            assert not await uow.store.find(GigInvitation, concert_id=event.id)
            assert not await uow.store.find(
                Membership, group_id=world.group.id, user_id=world.stranger.id
            )


async def test_lineup_confirmation_rechecks_telegram_reference(engine, world):
    await setup_workspace(engine, world)
    band = str(world.group.id)
    async with PostgresUnitOfWork(engine) as uow:
        target = await uow.store.get(User, world.stranger.id)
        target.username, target.username_seen_at = "performer", now()
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        session = await post(client, 1, "open_repertoire", {"band": band})
        plan = await post(
            client,
            1,
            "staff_lineup_preview",
            {
                "band": band,
                "name": "Шаблон",
                "quick": "Бас\nГитара: @performer",
            },
        )
        async with PostgresUnitOfWork(engine) as uow:
            target = await uow.store.get(User, world.member.id)
            target.username, target.username_seen_at = "performer", now()
        result = await action(client, engine, session, "lineup_create", succeeds=False, **plan)
        assert result["error"] == "conflict"
        assert (await get(client, 1, "staff_directory", band=band, section="lineups"))["rows"] == []
