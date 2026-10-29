from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message
from test_catalog_articles import ArticleTelegram

from setlist_bot.application.collectives import Collectives
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.entities import ChatBinding, Confirmation, Group, Membership, Outbox, now
from setlist_bot.domain.rules import SuggestionRestricted, pause_item
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.transport.articles import setlist_articles
from setlist_bot.transport.telegram import Processor, send_response


def client(engine, *, analytics=None):
    updates = count(80000)
    processor = Processor(engine, bot_username="test_bot", analytics=analytics)

    async def send(
        text,
        *,
        actor=1,
        chat=-100,
        private=False,
        owner=None,
        reply=None,
        remember=None,
        source=None,
    ):
        target = actor if private else chat
        response = await processor.process(
            next(updates),
            actor,
            target,
            text,
            private=private,
            chat_owner_id=owner,
            reply_message_id=reply,
            message_id=source,
            is_callback=not text.startswith("/") and reply is None,
        )
        if response and remember:
            await processor.remember_response(
                target,
                response,
                [
                    Message(
                        message_id=remember,
                        date=datetime.now(UTC),
                        chat=Chat(id=target, type="private" if private else "supergroup"),
                    )
                ],
            )
        return response

    return send


def action(response, prefix):
    return next(data for _, data in response.buttons if data.startswith(prefix + " "))


async def test_worker_can_register_but_only_verified_chat_owner_can_create(engine, world):
    send = client(engine)
    prompt = await send("/group", actor=2, chat=-300, remember=10)
    assert prompt.force_reply
    preview = await send("Туровая группа", actor=2, chat=-300, reply=10)
    assert "владелец" in preview.text and "администратором" in preview.text
    confirm = action(preview, "group_create_yes")
    denied = await send(confirm, actor=2, chat=-300, owner=1)
    assert "только владелец" in denied.text
    unknown = await send(confirm, actor=1, chat=-300)
    assert "Не удалось определить" in unknown.text
    result = await send(confirm, actor=1, chat=-300, owner=1)
    assert "Создан коллектив" in result.text
    async with PostgresUnitOfWork(engine) as uow:
        group = (await uow.store.find(Group, name="Туровая группа"))[0]
        assert group.owner_id == world.admin.id
        roles = await uow.store.find(Membership, group_id=group.id)
        assert {m.user_id for m in roles} == {world.admin.id, world.member.id}
        assert all(m.role == "admin" and m.status == "active" for m in roles)
    repeated = await send(confirm, actor=1, chat=-300, owner=1)
    assert "Создан коллектив" not in repeated.text


async def test_restriction_covers_commands_old_private_drafts_and_owner_requests(engine, world):
    send = client(engine)
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    old_draft = await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.member).edit_songs(
            world.group.id, world.concert.id, 0, ["Комета"], private=True
        )
        (await uow.store.get(Group, world.group.id)).admins_only_suggestions = True
    denied = await send("/publish", actor=2, private=True, remember=23)
    assert "только администраторы" in denied.text and len(denied.buttons) == 2
    notice = action(denied, "cr_request")
    response = await send(notice, actor=2, private=True)
    assert set(response.delete_message_ids) == {23}
    assert response.callback_notice and not response.text
    denied_again = await send(action(old_draft, "copy_list"), actor=2, private=True, remember=24)
    await send(action(denied_again, "cr_request"), actor=2, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        queued = await uow.store.find(Outbox, support_kind="collective")
        assert len(queued) == 1 and queued[0].chat_id == 1
        yes = queued[0].buttons[0][1]
    foreign = await send(yes, actor=3, private=True)
    assert "Вам предоставлены" not in foreign.text
    await send(yes, private=True)
    async with PostgresUnitOfWork(engine) as uow:
        member = (
            await uow.store.find(Membership, group_id=world.group.id, user_id=world.member.id)
        )[0]
        assert (
            member.role == "admin"
            and (await uow.store.get(Group, world.group.id)).owner_id == world.admin.id
        )
    repeated = await send(yes, private=True)
    assert "устарел" in repeated.text
    accepted = await send("/publish", actor=2, private=True)
    assert "только администраторы" not in accepted.text


async def test_restriction_blocks_service_mutations_and_cancel_preserves_roles(engine, world):
    async with PostgresUnitOfWork(engine) as uow:
        (await uow.store.get(Group, world.group.id)).admins_only_suggestions = True
    async with PostgresUnitOfWork(engine) as uow:
        service = Setlists(uow.store, world.member)
        with pytest.raises(SuggestionRestricted):
            await service.edit_songs(world.group.id, world.concert.id, 0, ["Комета"], private=True)
        with pytest.raises(SuggestionRestricted):
            await service.publish(world.group.id, world.concert.id)
    send = client(engine)
    await send(f"/use_concert {world.concert.id}", actor=2)
    notice = await send("/suggestion", actor=2, remember=24)
    cancel = await send(action(notice, "cr_cancel"), actor=2)
    assert set(cancel.delete_message_ids) == {24} and not cancel.text
    async with PostgresUnitOfWork(engine) as uow:
        assert not await uow.store.find(Outbox, support_kind="collective")


async def test_group_style_changes_all_views_without_mutating_setlist_or_pdf_settings(
    engine, world
):
    send = client(engine)
    shown = await send(f"/use_concert {world.concert.id}", remember=30)
    options = await send(action(shown, "slmenu"), source=30)
    settings = await send(action(options, "slsettings"), source=30)
    article = await send(next(a for _, a in settings.buttons if a.endswith(" article")), source=30)
    assert article.articles and article.articles_keyboard
    assert article.articles[0].blocks[0]["type"] == "paragraph"
    assert len(article.articles[0].blocks[1]["cells"][0]) == 3
    await send(f"/use_group {world.group.id}", actor=2, private=True)
    private = await send(f"/use_concert {world.concert.id}", actor=2, private=True)
    assert private.articles
    other = await send(f"/use_concert {world.other_concert.id}", chat=-200)
    assert not other.articles
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.get(Group, world.group.id)).setlist_style == "article"
        _, version, approved = await Setlists(uow.store, world.admin).read(
            world.group.id, world.concert.id
        )
        assert version == 0 and not approved
        assert (await uow.store.get(type(world.admin), world.admin.id)).export_settings == {}
    # The Article schema and attached navigation must be accepted by the Telegram model.
    session = ArticleTelegram()
    async with Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_TOKEN", session=session) as bot:
        await send_response(bot, -100, article)


async def test_transfer_requires_both_owners_and_preserves_data_and_old_chat_until_accept(
    engine, world
):
    send = client(engine)
    old = await send(f"/use_concert {world.concert.id}", remember=90)
    async with PostgresUnitOfWork(engine) as uow:
        service = Collectives(uow.store, world.admin, 1)
        token = (await service.begin_transfer(world.group.id)).id
        counts_before = {table: len(await uow.store.find(table)) for table in (Group, Membership)}
    denied = await send(f"/chat_transfer {token.hex}", actor=2, chat=-300)
    assert "Привязать коллектив" not in str(denied.buttons)
    review = await send(f"/chat_transfer {token.hex}", chat=-300)
    assert "Владельцем коллектива останется" in review.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(ChatBinding, group_id=world.group.id))[0].chat_id == -100
    rejected = await send(action(review, "ct_yes"), chat=-300, owner=3)
    assert "только владелец" in rejected.text
    done = await send(action(review, "ct_yes"), actor=3, chat=-300, owner=3)
    assert "перенесён" in done.text
    async with PostgresUnitOfWork(engine) as uow:
        group = await uow.store.get(Group, world.group.id)
        assert group.owner_id == world.admin.id
        assert (await uow.store.find(ChatBinding, group_id=group.id))[0].chat_id == -300
        counts_after = {kind: len(await uow.store.find(kind)) for kind in counts_before}
        assert counts_after == counts_before
        assert not await uow.store.find(Membership, group_id=group.id, user_id=world.stranger.id)
    stale = await send(action(old, "slmenu"), source=90)
    assert not stale.markup_only
    assert (
        "перенесён" not in (await send(action(review, "ct_yes"), actor=3, chat=-300, owner=3)).text
    )
    assert any(str(world.concert.id) in a for _, a in (await send("/concerts", chat=-300)).buttons)


@pytest.mark.parametrize("reason", ["expired", "cancelled", "occupied", "changed_owner"])
async def test_invalid_transfer_never_detaches_previous_chat(engine, world, reason):
    send = client(engine)
    async with PostgresUnitOfWork(engine) as uow:
        token = (await Collectives(uow.store, world.admin, 1).begin_transfer(world.group.id)).id
    review = await send(f"/chat_transfer {token.hex}", chat=-300)
    async with PostgresUnitOfWork(engine) as uow:
        row = await uow.store.get(Confirmation, token)
        if reason == "expired":
            row.expires_at = now() - timedelta(seconds=1)
        elif reason == "changed_owner":
            (await uow.store.get(Group, world.group.id)).owner_id = world.member.id
        elif reason == "occupied":
            binding = (await uow.store.find(ChatBinding, group_id=world.other.id))[0]
            binding.chat_id = -300
    if reason == "cancelled":
        await send(action(review, "ct_cancel"), chat=-300)
    result = await send(action(review, "ct_yes"), actor=3, chat=-300, owner=3)
    assert "перенесён" not in result.text
    async with PostgresUnitOfWork(engine) as uow:
        assert (await uow.store.find(ChatBinding, group_id=world.group.id))[0].chat_id == -100


def test_article_pause_has_empty_number_and_continuous_song_numbers():
    snapshot = {
        "default_pause_seconds": 5,
        "blocks": [
            {
                "items": [
                    {"title": "A", "duration_seconds": 60, "pause_before": None},
                    pause_item(180),
                    {"title": "B", "duration_seconds": 60, "pause_before": None},
                ]
            }
        ],
    }
    article = setlist_articles("Heading", snapshot, "ru")[0]
    rows = article.blocks[1]["cells"]
    assert [row[0]["text"] for row in rows] == ["№", "1", "", "2", ""]
    assert rows[2][1]["text"] == "пауза"
    assert rows[-1][2]["text"] == "05:00"
