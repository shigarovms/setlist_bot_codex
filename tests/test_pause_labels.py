"""Pause labels survive the existing draft, approval and export pipeline."""

from copy import deepcopy
from uuid import uuid4

import pytest
from test_miniapp import session, song
from test_pdf import reader, sample_payload, text

from setlist_bot.application.miniapp import MiniDrafts
from setlist_bot.application.setlists import Setlists
from setlist_bot.domain.display import entry_labels, pause_title
from setlist_bot.domain.entities import Item, PersonalDraft
from setlist_bot.domain.rules import AppError, editable_content, flat_content, pause_item, totals
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.infrastructure.pdf import PdfDocuments
from setlist_bot.transport.articles import setlist_articles
from setlist_bot.transport.render import render_setlist


def test_empty_custom_and_default_labels_are_distinct_without_changing_timing():
    payload = sample_payload()["snapshot"]
    payload["blocks"][0]["items"] = [
        pause_item(180, "Крабы & <микрофон>"),
        pause_item(30, ""),
        pause_item(20),
    ]
    original = deepcopy(payload)
    normalized = flat_content(payload)
    assert normalized == payload and original == payload
    assert totals(normalized)[2] == 230
    assert entry_labels(normalized["blocks"][0]["items"]) == [" ", " ", " "]
    changed = deepcopy(payload)
    changed["blocks"][0]["items"][0] = pause_item(180, "")
    assert editable_content(changed) != editable_content(payload)
    assert pause_title("en", label="") == ""
    assert pause_title("en") == "pause"


@pytest.mark.parametrize("audience", ["musicians", "lights"])
@pytest.mark.parametrize(
    "label",
    ["Крабы уносят микрофонную стойку", "", None, ("Крабы танцуют и уносят стойку. " * 30).strip()],
    ids=["custom", "empty", "default", "long"],
)
def test_pdf_and_article_keep_literal_or_blank_pause_labels(audience, label):
    payload = sample_payload()
    payload["snapshot"]["blocks"][0]["items"] = [pause_item(180, label)]
    pdf_text = " ".join(text(reader(PdfDocuments().render(payload, audience, "ru", False))).split())
    assert ("03:00" in pdf_text) == (audience != "musicians" or label != "")
    if label:
        if len(label) > 200:
            # Repeated page headers interrupt a long paragraph, but no words may disappear.
            for word in set(label.split()):
                assert pdf_text.split().count(word) == label.split().count(word)
        else:
            assert label in pdf_text
    elif label == "":
        assert "пауза" not in pdf_text.replace("Средняя пауза", "")
    else:
        assert "пауза" in pdf_text
    page = setlist_articles("Заголовок", payload["snapshot"], "ru")[0]
    cell = page.blocks[-1]["cells"][1][1]["text"]
    assert cell == ("пауза" if label is None else label)


async def test_draft_label_only_edit_publish_apply_approve_history_and_reload(engine, world):
    identifier, initial = await session(engine, world)
    values = [
        song(world),
        {"kind": "pause", "duration_seconds": 180, "pause_label": "Крабы & <микрофон>"},
        {"kind": "pause", "duration_seconds": 30, "pause_label": ""},
        song(world),
    ]
    async with PostgresUnitOfWork(engine) as uow:
        service = MiniDrafts(uow.store, world.admin)
        saved = await service.save(identifier, initial["version"], values, str(uuid4()))
        assert saved["entries"][1]["pause_label"] == "Крабы & <микрофон>"
        assert saved["entries"][2]["pause_label"] == ""
        lists = Setlists(uow.store, world.admin)
        submission = await lists.publish(world.group.id, world.concert.id)
        await lists.apply(
            world.group.id, world.concert.id, submission.id, world.concert.lifecycle_version
        )
        snapshot, version, _ = await lists.read(world.group.id, world.concert.id)
        assert snapshot["blocks"][0]["items"][1]["pause_label"] == values[1]["pause_label"]
        await lists.approve(world.group.id, world.concert.id, version)
    async with PostgresUnitOfWork(engine) as uow:
        lists = Setlists(uow.store, world.admin)
        snapshot, version, approved = await lists.read(world.group.id, world.concert.id)
        assert approved and snapshot["blocks"][0]["items"][2]["title"] == ""
        output = render_setlist(snapshot, world.concert, "ru", version, approved=True)
        assert "Крабы" in output and "пауза" not in output
        rows = await uow.store.find(Item, group_id=world.group.id)
        assert sorted(row.pause_label for row in rows if row.pause_label is not None) == [
            "",
            values[1]["pause_label"],
        ]
        await lists.reopen(world.group.id, world.concert.id, version)
        working, _, _ = await lists.read(world.group.id, world.concert.id)
        assert working["blocks"][0]["items"][1]["pause_label"] == values[1]["pause_label"]
        state = await MiniDrafts(uow.store, world.admin).state(identifier)
        values[1]["pause_label"] = ""
        request = str(uuid4())
        updated = await MiniDrafts(uow.store, world.admin).save(
            identifier, state["version"], values, request
        )
        assert updated == await MiniDrafts(uow.store, world.admin).save(
            identifier, state["version"], values, request
        )
        assert updated["version"] == state["version"] + 1
        assert updated["entries"][1]["pause_label"] == ""
        assert editable_content(working) != editable_content(
            (await lists.read(world.group.id, world.concert.id, private=True))[0]
        )


@pytest.mark.parametrize("invalid", [True, 123, "x" * 1001, "bad\x00text"])
async def test_invalid_labels_cannot_change_saved_draft(engine, world, invalid):
    identifier, initial = await session(engine, world)
    with pytest.raises(AppError, match="pause_label"):
        async with PostgresUnitOfWork(engine) as uow:
            await MiniDrafts(uow.store, world.admin).save(
                identifier,
                initial["version"],
                [{"kind": "pause", "duration_seconds": 60, "pause_label": invalid}],
                str(uuid4()),
            )
    async with PostgresUnitOfWork(engine) as uow:
        state = await MiniDrafts(uow.store, world.admin).state(identifier)
        assert state["version"] == initial["version"] and not state["entries"]


async def test_pause_label_migration_preserves_old_pause_and_refuses_loss_of_private_notes(
    engine, world, database_url
):
    from test_migrations import alembic

    async with PostgresUnitOfWork(engine) as uow:
        await Setlists(uow.store, world.admin).edit_songs(
            world.group.id, world.concert.id, 0, ["Комета", "3 мин"]
        )
    alembic(database_url, "downgrade", "a1426785384d")
    alembic(database_url, "upgrade", "head")
    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        state = await MiniDrafts(uow.store, world.admin).state(identifier)
        assert "pause_label" not in state["entries"][1]
        await MiniDrafts(uow.store, world.admin).save(
            identifier,
            initial["version"],
            [{"kind": "pause", "duration_seconds": 60, "pause_label": ""}],
            str(uuid4()),
        )
    refused = alembic(database_url, "downgrade", "a1426785384d", check=False)
    assert refused.returncode != 0 and "would lose pause labels" in refused.stderr
    async with PostgresUnitOfWork(engine) as uow:
        draft = (await uow.store.find(PersonalDraft, owner_id=world.admin.id))[0]
        assert draft.content["blocks"][0]["items"][0]["pause_label"] == ""


async def test_many_long_labels_fit_a_bounded_authenticated_request(engine, world):
    import json

    from aiohttp.test_utils import TestClient, TestServer
    from test_miniapp import settings, signed

    from setlist_bot.domain.entities import DeploymentIdentity
    from setlist_bot.miniapp.app import create_app

    identifier, initial = await session(engine, world)
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))
    config = settings(engine)
    payload = {
        "session": str(identifier),
        "version": initial["version"],
        "request_id": str(uuid4()),
        "entries": [
            {"kind": "pause", "duration_seconds": 1, "pause_label": "Я" * 1000} for _ in range(200)
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    assert 100 * 1024 < len(body) < 1024 * 1024
    headers = {
        "Authorization": "tma " + signed(),
        "Origin": config.public_base_url,
        "Content-Type": "application/json",
    }
    async with TestClient(TestServer(create_app(config, engine))) as client:
        saved = await client.post("/miniapp/api/save", data=body, headers=headers)
        assert saved.status == 200, await saved.text()
        assert len((await saved.json())["entries"]) == 200
        too_big = await client.post(
            "/miniapp/api/save", data=b" " * (1024 * 1024 + 1), headers=headers
        )
        assert too_big.status == 413
