from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_miniapp import settings, signed, song

from setlist_bot.application.setlists import Setlists
from setlist_bot.application.templates import Templates
from setlist_bot.domain.entities import DeploymentIdentity, Outbox, PersonalDraft, Submission
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app
from setlist_bot.transport.telegram import Processor


async def template(engine, world, private=False):
    async with PostgresUnitOfWork(engine) as uow:
        snapshot, version, _ = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        row = Templates(uow.store, world.admin, 1 if private else -100).create(
            world.group.id,
            world.concert.id,
            snapshot,
            private=private,
            version=version,
            group_epoch=world.group.lifecycle_version,
            concert_epoch=world.concert.lifecycle_version,
        )
        return row.id


@pytest.mark.usefixtures("invited_member")
async def test_public_launch_is_personal_and_target_is_taken_from_signed_telegram_data(
    engine, world
):
    identifier = await template(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    async with TestClient(TestServer(create_app(settings(engine), engine))) as client:
        sessions = []
        for actor in (1, 2, 1):
            headers = {
                "Authorization": "tma "
                + signed(actor=actor, chat_type="supergroup", start_param="sl_" + identifier.hex)
            }
            response = await client.get(
                "/miniapp/api/launch", headers=headers, params={"launch": str(uuid4())}
            )
            assert response.status == 200, await response.text()
            sessions.append((await response.json())["session"])
        assert sessions[0] != sessions[1] and sessions[0] == sessions[2]
        denied = await client.get(
            "/miniapp/api/state",
            params={"session": sessions[0]},
            headers={"Authorization": "tma " + signed(actor=2, chat_type="supergroup")},
        )
        assert denied.status == 401
        denied = await client.get(
            "/miniapp/api/launch",
            headers={"Authorization": "tma " + signed(actor=3, start_param="sl_" + identifier.hex)},
        )
        assert denied.status == 403
        assert (
            await client.get(
                "/miniapp/api/launch",
                params={"launch": identifier.hex},
                headers={"Authorization": "tma " + signed()},
            )
        ).status == 401
    async with PostgresUnitOfWork(engine) as uow:
        assert len(await uow.store.find(PersonalDraft)) == 2
        assert not await uow.store.find(Submission) and not await uow.store.find(Outbox)


@pytest.mark.usefixtures("invited_member")
async def test_method_menu_has_editor_first_and_text_list_is_sent_only_once(engine, world):
    p = Processor(
        engine,
        bot_username="botToPract696_bot",
        miniapp_public_base_url="https://example.org",
        setlist_legacy_input_enabled=True,
    )
    await p.process(1, 1, -100, f"/use_concert {world.concert.id}", private=False)
    group = await p.process(2, 1, -100, "/suggestion", private=False)
    assert group.buttons[0][0] == "🖐 Открыть редактор" and "?startapp=sl_" in group.buttons[0][1]
    assert not group.deliveries and not group.force_reply
    link = next(a for _, a in group.buttons if "?start=draft_" in a)
    private = await p.process(3, 2, 2, "/start " + link.split("?start=")[1], private=True)
    assert private.buttons[0][0] == "🖐 Открыть редактор" and private.web_apps
    assert not private.deliveries
    text = await p.process(
        4, 2, 2, next(a for _, a in private.buttons if a.startswith("pick_text ")), private=True
    )
    assert text.force_reply and "Длительность предлагаемого" in text.text
    assert not text.deliveries
    group_text = await p.process(
        5, 2, -100, next(a for _, a in group.buttons if a.startswith("copy_text ")), private=False
    )
    assert group_text.force_reply and not group_text.deliveries


@pytest.mark.usefixtures("invited_member")
async def test_publish_is_explicit_idempotent_and_waits_for_worker_delivery(engine, world):
    from aiogram import Bot
    from test_miniapp import session
    from test_telegram import FakeTelegram

    from setlist_bot.main import deliver_one_outbox

    identifier, initial = await session(engine, world, member=True)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    config = settings(engine)
    headers = {"Authorization": "tma " + signed(actor=2), "Origin": config.public_base_url}
    async with TestClient(TestServer(create_app(config, engine))) as client:
        payload = {
            "session": str(identifier),
            "version": initial["version"],
            "entries": [song(world)],
            "request_id": str(uuid4()),
        }
        response = await client.post("/miniapp/api/publish", json=payload, headers=headers)
        assert response.status == 200, await response.text()
        assert (await response.json())["status"] == "pending"
        again = await client.post("/miniapp/api/publish", json=payload, headers=headers)
        assert again.status == 200 and (await again.json())["status"] == "pending"
        async with PostgresUnitOfWork(engine) as uow:
            assert (
                len(await uow.store.find(Submission)) == 1
                and len(await uow.store.find(Outbox)) == 1
            )
            assert not (
                await Setlists(uow.store, world.admin).read(world.group.id, world.concert.id)
            )[0]["blocks"][0]["items"]
        bot = Bot("123456:TEST", session=FakeTelegram())
        assert await deliver_one_outbox(bot, engine)
        status = await client.get(
            "/miniapp/api/publication", params={"session": str(identifier)}, headers=headers
        )
        assert (await status.json())["status"] == "delivered"
        await bot.session.close()
