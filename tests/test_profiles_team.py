import json
from datetime import timedelta

import pytest
from test_setlist_menus import sender

from setlist_bot.application.profiles import Profiles
from setlist_bot.application.staffing import Staffing
from setlist_bot.domain.entities import (
    Confirmation,
    GigInvitation,
    Membership,
    StaffContact,
    User,
    now,
)
from setlist_bot.domain.rules import AppError
from setlist_bot.domain.staff_roles import matches, parse_skills, role_key
from setlist_bot.infrastructure.database import PostgresUnitOfWork


@pytest.mark.parametrize(
    "left,right",
    [
        ("бас", "bass player"),
        ("турик", "tour manager"),
        ("свет", "lighting designer"),
        ("ударник", "drummer"),
        ("мониторщик", "MON"),
        ("звукарь", "sound engineer"),
    ],
)
def test_professional_aliases_are_exact_and_bilingual(left, right):
    assert role_key(left) == role_key(right)
    assert matches(left, right)


def test_specific_skills_are_never_inferred_from_general_sound():
    assert matches("звук", "MON")
    assert not matches("MON", "FOH")
    assert not matches("FOH", "звукарь")
    assert not matches("бас", "бас-бочка")
    assert parse_skills("bass player, басист, FOH, MON") == [
        "Бас-гитара",
        "Звукорежиссёр FOH",
        "Мониторный звукорежиссёр",
    ]
    for value in ("звук", "фон", "TM"):
        with pytest.raises(AppError, match="profile_roles_ambiguous"):
            parse_skills(value)


async def test_profile_questionnaire_exact_replies_confirmation_and_repeat(engine, world):
    send = sender(engine)
    welcome = await send("/start", private=True)
    assert ("👤 Мой профиль", "profile") in welcome.buttons
    first = await send("profile", private=True, remember=100)
    assert not first.force_reply
    assert first.text.count("/cancel") == 1
    assert first.text.startswith("Имя · шаг 1 из 5")
    assert (
        "Как вас зовут или как бы вы хотели, чтобы вас представляли в конце выступления?"
        in first.text
    )
    roles = await send("Илья Сценический", private=True, reply=100, remember=101)
    assert "Какие роли" in roles.text
    clarify = await send("звук, MON, звуковой техник", private=True, reply=101, remember=102)
    assert not clarify.force_reply and "Какую позицию" in clarify.text
    foh = next(action for label, action in clarify.buttons if "FOH" in label)
    city = await send(foh, private=True, remember=103)
    assert "городе" in city.text
    available = await send("Москва", private=True, reply=103, remember=104)
    enabled = await send(available.buttons[0][1], private=True, remember=105)
    stale = await send(available.buttons[0][1], private=True)
    assert "устар" in stale.text.lower() or "заново" in stale.text.lower()
    await send(
        next(action for label, action in enabled.buttons if label == "Далее"),
        private=True,
        remember=106,
    )
    confirm = await send("-", private=True, reply=106, remember=107)
    async with PostgresUnitOfWork(engine) as uow:
        user = await uow.store.get(User, world.admin.id)
        assert user.profile == {} and user.profile_version == 0
    saved = await send(confirm.buttons[0][1], private=True)
    assert "Профиль сохранён" in saved.text
    await send(confirm.buttons[0][1], private=True)
    await send("/start", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        user = await uow.store.get(User, world.admin.id)
        assert user.profile_version == 1
        assert user.display_name == "Илья Сценический"
        assert user.profile["substitutions"] is True
        assert user.profile["city"] == "Москва"
        assert user.profile["about"] == ""
        assert user.profile["roles"] == [
            "Звукорежиссёр FOH",
            "Мониторный звукорежиссёр",
            "Звуковой техник",
        ]


async def test_profile_cancel_stale_version_and_other_user_cannot_save(engine, world):
    send = sender(engine)
    await send("profile", private=True, remember=120)
    await send("Не сохранять", private=True, reply=120, remember=121)
    await send("/cancel", private=True)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(User, world.admin.id)).profile == {}
        await Profiles(uow.store, world.admin).save(0, {"name": "Сохранённое имя", "roles": []})
        with pytest.raises(AppError, match="conflict"):
            await Profiles(uow.store, world.admin).save(0, {"name": "Не перезаписать"})
    await send("profile", private=True, remember=122)
    await send("Чужой ответ", actor=2, private=True, reply=122)
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(User, world.admin.id)).profile["name"] == "Сохранённое имя"
        rows = await uow.store.find(Confirmation, user_id=world.admin.id)
        session = next(r for r in rows if json.loads(r.command).get("kind") == "profile_input")
        session.expires_at = now() - timedelta(seconds=1)
    denied = await send("Поздний ответ", private=True, reply=122)
    assert denied is None or not denied.force_reply


async def test_permanent_defaults_are_group_scoped_and_do_not_rewrite_existing_gigs(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Басист")
        assert (await staff.role(world.group.id, "bass player")).id == role.id
        lineup = await staff.template(world.group.id, "Большой", [{"role_id": str(role.id)}])
        first = await staff.set_permanent(world.group.id, role.id, world.member.id)
        slots = await staff.apply_template(world.concert.id, lineup.id, lineup.version)
        assert slots[0].preferred_user_id == world.member.id
        await staff.set_permanent(world.group.id, role.id, world.stranger.id)
        await staff.change_contact(first.id, first.version, remove=True)
        next_slots = await staff.apply_template(world.concert2.id, lineup.id, lineup.version)
        assert next_slots[0].preferred_user_id == world.stranger.id
        assert slots[0].preferred_user_id == world.member.id
        assert not await uow.store.find(GigInvitation)
        assert not await uow.store.find(
            Membership, group_id=world.group.id, user_id=world.stranger.id
        )
        with pytest.raises(AppError):
            await Staffing(uow.store, world.member).set_permanent(
                world.group.id, role.id, world.member.id
            )
        other_role = await staff.role(world.other.id, "Бас")
        with pytest.raises(AppError):
            await staff.set_permanent(world.group.id, other_role.id, world.member.id)


async def test_substitutions_use_all_declared_skills_only_among_local_contacts(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        foh = await staff.role(world.group.id, "FOH")
        mon = await staff.role(world.group.id, "MON")
        await staff.set_permanent(world.group.id, foh.id, world.member.id)
        slot = await staff.add_slot(world.concert.id, mon.id)
        assert await staff.candidates(slot.id) == []
        await Profiles(uow.store, world.member).save(
            0, {"roles": parse_skills("FOH, MON"), "substitutions": True}
        )
        await Profiles(uow.store, world.stranger).save(
            0, {"roles": parse_skills("MON"), "substitutions": True}
        )
        assert [u.id for _, u, _, _ in await staff.candidates(slot.id)] == [world.member.id]
        await staff.contact(world.group.id, mon.id, world.member.id, "hidden")
        assert await staff.candidates(slot.id) == []


async def test_new_team_menu_permanent_workflow_and_template_order(engine, world):
    send = sender(engine)
    team = await send("/team")
    assert [name for name, _ in team.buttons] == [
        "Постоянная команда",
        "Знакомые исполнители",
        "Составы",
        "Участники и права",
        "◀️ Назад",
    ]
    members = await send(team.buttons[0][1])
    await send(members.buttons[0][1], remember=150)
    listed = await send("Басист 2", reply=150)
    assert "Басист" in listed.text
    async with PostgresUnitOfWork(engine) as uow:
        contact = (await uow.store.find(StaffContact, group_id=world.group.id))[0]
        assert contact.permanent
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Гитара")
        old = await staff.template(world.group.id, "Я старый", [{"role_id": str(role.id)}])
        new = await staff.template(world.group.id, "А новый", [{"role_id": str(role.id)}])
        old.created_at = now() - timedelta(days=2)
        new.created_at = now() - timedelta(days=1)
        await staff.edit_template(old.id, old.version, name="Ъ переименованный")
    templates = await send(team.buttons[2][1])
    assert [name for name, _ in templates.buttons][:2] == ["Ъ переименованный", "А новый"]


async def test_migration_recovers_permanent_defaults_and_creation_dates_without_changing_bookings(
    engine, world, database_url
):
    from test_migrations import alembic

    from setlist_bot.domain.entities import LineupTemplate

    async with PostgresUnitOfWork(engine) as uow:
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "Бас-гитара")
        template = await staff.template(
            world.group.id,
            "Существующий",
            [{"role_id": str(role.id), "user_id": str(world.member.id)}],
        )
        contact = (await uow.store.find(StaffContact, group_id=world.group.id))[0]
        contact.permanent, contact.priority = False, 100
        slot = await staff.add_slot(world.concert.id, role.id)
        invite = await staff.invite(slot.id, world.member.id, slot.version)
        before = (invite.id, invite.status, slot.id, template.id)
    alembic(database_url, "downgrade", "d71f439e8a20")
    try:
        alembic(database_url, "upgrade", "head")
    finally:
        alembic(database_url, "upgrade", "head")
    async with PostgresUnitOfWork(engine) as uow:
        restored = (await uow.store.find(StaffContact, group_id=world.group.id))[0]
        assert restored.permanent
        event = await uow.store.get(GigInvitation, before[0])
        assert (event.id, event.status, event.slot_id) == before[:3]
        assert (await uow.store.get(LineupTemplate, before[3])).created_at <= now()
        assert (await uow.store.get(User, world.member.id)).profile == {}


async def test_profile_parallel_saves_do_not_overwrite(engine, world):
    import asyncio

    async def save(name):
        try:
            async with PostgresUnitOfWork(engine) as uow:
                await Profiles(uow.store, world.admin).save(0, {"name": name})
            return "saved"
        except AppError as exc:
            return exc.code

    assert sorted(await asyncio.gather(save("Первый"), save("Второй"))) == ["conflict", "saved"]


async def test_profile_fields_can_be_kept_and_admin_views_stay_group_scoped(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        await Profiles(uow.store, world.member).save(
            0,
            {
                "name": "Имя со сцены",
                "roles": parse_skills("FOH, MON"),
                "city": "Пермь",
                "about": "Готов работать в туре",
            },
        )
        staff = Staffing(uow.store, world.admin)
        role = await staff.role(world.group.id, "FOH")
        await staff.set_permanent(world.group.id, role.id, world.member.id)
    send = sender(engine)
    profile = await send("profile", actor=2, private=True, remember=171)
    assert "FOH" in profile.text and "Пермь" in profile.text
    city = next(action for label, action in profile.buttons if label == "Изменить город")
    await send(city, actor=2, private=True, remember=172)
    same = await send("=", actor=2, private=True, reply=172)
    assert "FOH" in same.text and "Пермь" in same.text
    await send("/cancel", actor=2, private=True)
    team = await send("/team")
    people = await send(team.buttons[0][1])
    card = await send(people.buttons[0][1])
    assert (
        "Пермь" in card.text and "Готов работать в туре" in card.text and "Мониторный" in card.text
    )
    denied = await send(people.buttons[0][1], actor=3)
    assert "Готов работать в туре" not in denied.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(User, world.member.id)).profile["name"] == "Имя со сцены"


async def test_profile_resumes_after_navigation_and_edits_one_field_with_custom_roles(
    engine, world
):
    from setlist_bot.transport.telegram import Processor

    send = sender(engine)
    first = await send("/profile", private=True)
    processor = Processor(engine)
    role_step = await processor.process(
        90000, 1, 1, "Илья", private=True, message_id=91, is_callback=False
    )
    assert not role_step.force_reply and "шаг 2" in role_step.text
    await send("/groups", private=True)
    assert (
        await processor.process(
            90001, 1, 1, "Посторонний текст", private=True, message_id=92, is_callback=False
        )
        is None
    )
    stale = await send(first.buttons[0][1], private=True)
    assert "устарел" in stale.text
    roles = await send("/profile", private=True, remember=100)
    assert "шаг 2" in roles.text
    sound = await send("басист, звукорежиссер, сценограф", private=True, reply=100)
    custom = await send(next(a for label, a in sound.buttons if "Оставить" in label), private=True)
    city = await send(next(a for label, a in custom.buttons if "Добавить" in label), private=True)
    avail = await send(city.buttons[0][1], private=True)
    about = await send(next(a for label, a in avail.buttons if label == "Далее"), private=True)
    confirm = await send(about.buttons[0][1], private=True)
    saved = await send(confirm.buttons[0][1], private=True)
    card = await send(saved.buttons[0][1], private=True)
    assert "Бас-гитара, Звукорежиссёр, сценограф" in card.text
    edit = await send(
        next(a for label, a in card.buttons if label == "Изменить город"),
        private=True,
        remember=101,
    )
    assert not edit.force_reply and "шаг" not in edit.text
    updated = await send("Казань", private=True, reply=101)
    assert "Казань" in updated.text and "Бас-гитара, Звукорежиссёр, сценограф" in updated.text


async def test_permanent_order_changes_primary_and_rejects_a_stale_move(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        service = Staffing(uow.store, world.admin)
        role = await service.role(world.group.id, "Бас")
        first = await service.set_permanent(world.group.id, role.id, world.member.id)
        second = await service.set_permanent(world.group.id, role.id, world.stranger.id)
        before = second.version
        assert await service.lineup_people(world.group.id, [role.id]) == [world.member.id]
        await service.move_permanent(second.id, before, -1)
        assert await service.lineup_people(world.group.id, [role.id]) == [world.stranger.id]
        with pytest.raises(AppError, match="input_expired"):
            await service.move_permanent(second.id, before, 1)
        await service.contact(world.group.id, role.id, world.stranger.id, "hidden")
        assert await service.lineup_people(world.group.id, [role.id]) == [world.member.id]
        assert first.permanent and second.permanent
