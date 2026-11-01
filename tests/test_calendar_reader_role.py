import importlib.util
import secrets
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from setlist_bot.calendar.app import ENGINE, create_app
from setlist_bot.calendar.settings import CalendarSettings
from setlist_bot.domain.entities import DeploymentIdentity
from setlist_bot.infrastructure.database import PostgresUnitOfWork


async def test_public_service_works_with_real_read_only_role_without_private_table_access(
    engine, world
):
    from test_calendars import issue

    _, token, _ = await issue(engine, world.member)
    spec = importlib.util.spec_from_file_location(
        "calendar_setup", Path(__file__).parents[1] / "scripts/setup-sandbox-calendar.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    role = "calendar_test_" + uuid4().hex[:12]
    password = secrets.token_hex(32)
    setup.ROLE = role
    database = engine.url.database
    assert database.endswith("_test")
    statements = setup.grants().replace("ON DATABASE setlist_sandbox", f"ON DATABASE {database}")
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        for statement in statements.split(";"):
            if statement.strip():
                await connection.execute(text(statement))
    settings = CalendarSettings(
        database_url=engine.url.set(username=role, password=password).render_as_string(
            hide_password=False
        ),
        expected_bot_id=1110437563,
        environment="sandbox",
        bot_username="botToPract696_bot",
    )
    app = create_app(settings)
    from test_calendars import KEY
    from test_staffing import invite, reply, setup_slots

    from setlist_bot.application.calendars import Calendars

    one, _ = await setup_slots(engine, world)
    appointment = await invite(engine, world.admin, one, world.member.id)
    await reply(engine, world.member, appointment)
    async with PostgresUnitOfWork(engine) as uow:
        _, personal_token = await Calendars(uow.store, world.member, KEY).create(None, "personal")
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get(f"/calendar/{token}.ics")
            assert response.status == 200 and "Коллектив А" in await response.text()
            assert (await client.get("/healthz")).status == 200
            personal = await client.get(f"/calendar/{personal_token}.ics")
            assert personal.status == 200
            assert "gig_" + appointment.hex in (await personal.text()).replace("\r\n ", "")
            for table in (
                "users",
                "personal_drafts",
                "support_messages",
                "admin_sessions",
                "busy_periods",
                "staff_contacts",
            ):
                async with app[ENGINE].connect() as connection:
                    with pytest.raises(DBAPIError):
                        await connection.execute(text(f"SELECT * FROM {table}"))
            async with app[ENGINE].connect() as connection:
                with pytest.raises(DBAPIError):
                    await connection.execute(text("DELETE FROM calendar_subscriptions"))
            async with app[ENGINE].connect() as connection:
                with pytest.raises(DBAPIError):
                    await connection.execute(
                        text("SELECT token_ciphertext FROM calendar_subscriptions")
                    )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP OWNED BY {role}"))
            await connection.execute(text(f"DROP ROLE {role}"))
