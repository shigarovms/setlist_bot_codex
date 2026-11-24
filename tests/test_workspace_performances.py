from datetime import timedelta
from uuid import UUID, uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings
from test_repertoire_workspace_actions import get, post
from test_repertoire_workspace_music import deliver_request
from test_workspace_shared_setlist import setup_workspace

from setlist_bot.application.concert_details import ConcertDetails
from setlist_bot.application.setlists import Setlists
from setlist_bot.application.staffing import Staffing
from setlist_bot.application.trash import RETENTION
from setlist_bot.domain.entities import Concert, GigInvitation, GigSlot, Membership, now
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app


async def step(client, engine, session, action, *, succeeds=True, **fields):
    payload = {**session, "action": action, "request_id": str(uuid4()), **fields}
    sent = await post(client, 1, "performance_action", payload)
    assert sent["status"] == "pending"
    assert await deliver_request(engine, sent["request"]) is succeeds
    receipt = await get(client, 1, "catalog_status", **session, request=sent["request"])
    assert await post(client, 1, "performance_action", payload) == receipt
    return receipt


def form(**changes):
    return {
        "date": "2027-02-05",
        "title": "Зимний концерт",
        "city": "Москва",
        "performance_type": "Сольник",
        "duration": "45",
        "info": "Площадка",
        **changes,
    }


def observed(row):
    return {"performance": row["id"], "version": row["version"], "epoch": row["epoch"]}


async def test_create_previews_lineups_and_is_idempotent(engine, world):
    await setup_workspace(engine, world)
    group = world.group.id
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(group, "Вокал")
        slots = [{"role_id": str(role.id), "user_id": str(world.member.id)}]
        main = await staff.template(group, "Основной", slots)
        await staff.template(group, "Малый", [{"role_id": str(role.id)}])
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": str(group)})
        member = await post(client, 2, "open_repertoire", {"band": str(group)})
        await post(
            client,
            2,
            "performance_action",
            {**member, "action": "create", "request_id": str(uuid4()), **form()},
            403,
        )
        choices = await step(client, engine, admin, "create", **form())
        assert choices["choose_lineup"] and [row["name"] for row in choices["lineups"]] == [
            "Основной",
            "Малый",
        ]
        quick = "05.02.2027  Название: Зимний концерт  Москва  45 мин  Состав: Основной"
        preview = await step(client, engine, admin, "create", quick=quick)
        assert preview["lineup"] == "Основной"
        assert preview["fields"]["date"] == "2027-02-05"
        assert preview["fields"]["duration"] == 2700
        assert preview["fields"]["city"] == "Москва"
        async with PostgresUnitOfWork(engine) as uow:
            assert not await uow.store.find(Concert, group_id=group, title="Зимний концерт")
            await Staffing(uow.store, world.admin).template(group, "Основной", slots)
        stale = await step(
            client, engine, admin, "confirm", succeeds=False, preview=preview["request"]
        )
        assert stale["error"] == "input_expired"
        preview = await step(client, engine, admin, "create", lineup=str(main.id), **form())
        other_session = await post(client, 1, "open_repertoire", {"band": str(group)})
        cross = await step(
            client, engine, other_session, "confirm", succeeds=False, preview=preview["request"]
        )
        assert cross["error"] == "input_expired"
        created = await step(client, engine, admin, "confirm", preview=preview["request"])
        second = await step(
            client, engine, admin, "confirm", succeeds=False, preview=preview["request"]
        )
        assert second["error"] == "input_expired"
        info = await get(
            client, 1, "performance", band=str(group), performance=created["performance"]
        )
        assert info["performance"]["planned_seconds"] == 2700
        async with PostgresUnitOfWork(engine) as uow:
            assert len(await uow.store.find(Concert, group_id=group, title="Зимний концерт")) == 1
            concert = UUID(created["performance"])
            slots_saved = await uow.store.find(GigSlot, concert_id=concert)
            assert len(slots_saved) == 1 and slots_saved[0].preferred_user_id == world.member.id
            assert not await uow.store.find(GigInvitation, concert_id=concert)


async def test_update_cancels_stale_preview_and_requests_date_reconfirmation(engine, world):
    await setup_workspace(engine, world)
    query = {"band": str(world.group.id), "performance": str(world.concert.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": query["band"]})
        info = (await get(client, 1, "performance", **query))["performance"]
        preview = await step(client, engine, admin, "update", **observed(info), **form())
        assert preview["date_changed"]
        async with PostgresUnitOfWork(engine) as uow:
            await ConcertDetails(uow.store, world.admin).update(
                world.group.id, world.concert.id, "concert_city", "Тула"
            )
        failed = await step(
            client, engine, admin, "confirm", succeeds=False, preview=preview["request"]
        )
        assert failed["error"] == "conflict"
        info = (await get(client, 1, "performance", **query))["performance"]
        assert info["city"] == "Тула" and info["date"] != "2027-02-05"
        preview = await step(client, engine, admin, "update", **observed(info), **form(duration=""))
        await step(client, engine, admin, "confirm", preview=preview["request"])
        changed = (await get(client, 1, "performance", **query))["performance"]
        assert changed["date"] == "2027-02-05" and changed["planned_seconds"] is None
        assert (changed["title"], changed["city"], changed["info"]) == (
            "Зимний концерт",
            "Москва",
            "Площадка",
        )
        async with PostgresUnitOfWork(engine) as uow:
            rows = await uow.store.find(
                GigInvitation, concert_id=world.concert.id, user_id=world.member.id
            )
            assert rows[0].status == "reconfirm"
            assert rows[0].date.isoformat() == changed["date"]


async def test_trash_revalidates_changes_permissions_and_retention(engine, world):
    await setup_workspace(engine, world)
    query = {"band": str(world.group.id), "performance": str(world.concert.id)}
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        admin = await post(client, 1, "open_repertoire", {"band": query["band"]})
        await get(client, 2, "performance_trash", 403, band=query["band"])
        info = (await get(client, 1, "performance", **query))["performance"]
        wrong = await step(
            client,
            engine,
            admin,
            "delete",
            succeeds=False,
            **{**observed(info), "performance": str(world.other_concert.id)},
        )
        assert wrong["error"] == "not_found"
        preview = await step(client, engine, admin, "delete", **observed(info))
        async with PostgresUnitOfWork(engine) as uow:
            await Setlists(uow.store, world.admin).edit_songs(
                world.group.id, world.concert.id, 0, ["Комета"]
            )
        stale = await step(
            client, engine, admin, "confirm", succeeds=False, preview=preview["request"]
        )
        assert stale["error"] == "delete_changed"
        preview = await step(client, engine, admin, "delete", **observed(info))
        await step(client, engine, admin, "confirm", preview=preview["request"])
        await get(client, 1, "performance", 403, **query)
        trash = (await get(client, 1, "performance_trash", band=query["band"]))["performances"]
        assert [row["id"] for row in trash] == [query["performance"]]
        restore = await step(
            client, engine, admin, "restore", performance=trash[0]["id"], epoch=trash[0]["epoch"]
        )
        await step(client, engine, admin, "confirm", preview=restore["request"])
        visible = await get(client, 1, "performance", **query)
        assert visible["setlist"]["entries"][0]["title"] == "Комета"
        assert not (await get(client, 1, "performance_trash", band=query["band"]))["performances"]

        preview = await step(client, engine, admin, "delete", **observed(visible["performance"]))
        queued = await post(
            client,
            1,
            "performance_action",
            {
                **admin,
                "action": "confirm",
                "preview": preview["request"],
                "request_id": str(uuid4()),
            },
        )
        async with PostgresUnitOfWork(engine) as uow:
            rows = await uow.store.find(Membership, group_id=world.group.id, user_id=world.admin.id)
            rows[0].role = "member"
        assert not await deliver_request(engine, queued["request"])
        async with PostgresUnitOfWork(engine) as uow:
            rows = await uow.store.find(Membership, group_id=world.group.id, user_id=world.admin.id)
            rows[0].role = "admin"
        await step(client, engine, admin, "confirm", preview=preview["request"])
        async with PostgresUnitOfWork(engine) as uow:
            concert = await uow.store.get(Concert, world.concert.id)
            concert.deleted_at = now() - RETENTION - timedelta(seconds=1)
            epoch = concert.lifecycle_version
        expired = await step(
            client,
            engine,
            admin,
            "restore",
            succeeds=False,
            performance=query["performance"],
            epoch=epoch,
        )
        assert expired["error"] == "trash_expired"
        assert not (await get(client, 1, "performance_trash", band=query["band"]))["performances"]
