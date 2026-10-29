"""Personal Article picker and explicit two-stage catalog import."""

from typing import Any
from uuid import UUID

from setlist_bot.application.collectives import Collectives
from setlist_bot.application.music_search import ACTIVE, MusicSearches, notify_search
from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import CatalogImport, ChatBinding, Group, MusicSearch
from setlist_bot.domain.music_discovery import PAGE_SIZE
from setlist_bot.domain.rules import clock, duration, require
from setlist_bot.infrastructure.music_sources import query_target
from setlist_bot.transport.articles import ArticlePage
from setlist_bot.transport.controller import Controller, Response


def methods(controller: Controller, *, group_id: UUID | None = None) -> list[tuple[str, str]]:
    buttons = [
        (controller.t("music_" + name), "mi_help " + name)
        for name in ("manual", "sheets", "telegram")
    ]
    if group_id is not None:
        buttons.insert(0, (controller.t("music_entry"), f"mi_open {group_id.hex}"))
    return buttons


def rich_button(label: str, action: str) -> dict[str, Any]:
    return {"type": "button", "button": {"text": label, "style": "link", "callback_data": action}}


async def remember_music(store: Store, response: Response, identifiers: list[int]) -> None:
    if response.music_search_id is None or not identifiers:
        return
    job = await store.get(MusicSearch, response.music_search_id, lock=True)
    if job.version == response.music_search_version:
        job.data = {
            **job.data,
            "panel_ids" if response.music_panel_private else "group_panel_ids": identifiers,
        }


async def music_view(controller: Controller, job: MusicSearch) -> Response:
    t = controller.t
    service = MusicSearches(controller.store, controller.user, controller.user.telegram_id)
    response = Response(
        "",
        music_search_id=job.id,
        music_search_version=job.version,
        navigation_slot="music",
        music_panel_private=controller.private,
    )
    response.edit_message_ids = list(
        job.data.get("panel_ids" if controller.private else "group_panel_ids", [])
    )
    cancel = (t("cancel_button"), f"mi_cancel {job.id.hex}")
    if job.status == "searching":
        response.text = t("music_wait")
        response.buttons = [(t("music_refresh"), f"mi_view {job.id.hex}"), cancel]
    elif job.status == "artists":
        response.text = t("music_artists")
        for index, artist in enumerate(job.data["artists"]):
            response.text += (
                "\n\n" + artist["name"] + (" · " + artist["genre"] if artist.get("genre") else "")
            )
            response.text += "\n" + " · ".join(artist.get("examples", []))
            response.buttons.append(
                (
                    artist["name"] + (" · " + artist["genre"] if artist.get("genre") else ""),
                    f"mi_artist {job.id.hex} {index}",
                )
            )
        response.buttons.append(cancel)
    elif job.status in ("choosing", "preview"):
        group = await controller.store.get(Group, job.group_id)
        choosing = job.status == "choosing"
        rows = job.data["tracks"] if choosing else job.data.get("preview_rows", [])
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(job.data.get("page", 0), 0), pages - 1)
        excluded = set(job.data.get("excluded", []))
        if choosing:
            response.text = t(
                "music_choose",
                group=group.name,
                count=len(rows),
                kept=len(rows) - len(excluded),
                excluded=len(excluded),
                page=page + 1,
                pages=pages,
            )
            if job.data.get("warnings"):
                response.text += "\n\n" + t("music_partial")
        else:
            response.text = t(
                "music_preview",
                group=group.name,
                count=job.data["new_count"],
                duplicates=job.data["duplicate_count"],
            )
            selected = [row for _, row in service.selected(job)]
            conflicts = sum(len(row.get("bpms", [])) > 1 for row in selected)
            found = sum(row["bpm"] is not None for row in selected)
            response.text += "\n\n" + t(
                "music_bpm_summary",
                found=found,
                missing=len(selected) - found - conflicts,
                conflicts=conflicts,
            )
        cells: list[list[dict[str, Any]]] = [
            [
                {"text": t("music_import_title"), "is_header": True},
                {"text": t("music_exclude") if choosing else "MM:SS · BPM", "is_header": True},
            ]
        ]
        fallback, row_buttons = [], []
        for index in range(page * PAGE_SIZE, min(len(rows), (page + 1) * PAGE_SIZE)):
            row = rows[index]
            if choosing:
                label = row["title"]
                seconds = job.data.get("durations", {}).get(str(index), row["seconds"])
                label += " · " + (clock(seconds) if seconds is not None else t("music_no_duration"))
                text: Any = {"type": "strikethrough", "text": label} if index in excluded else label
                symbol = "⬜" if index in excluded else "✅"
                action = f"mi_toggle {job.id.hex} {index}"
                cells.append([{"text": text}, {"text": rich_button(symbol, action)}])
                fallback.append(("[−] " if index in excluded else "[+] ") + label)
                row_buttons.append((symbol + " " + row["title"][:70], action))
            else:
                cells.append(
                    [
                        {"text": row["title"]},
                        {
                            "text": clock(row["duration_seconds"])
                            + " · "
                            + str(row.get("bpm") or "—")
                        },
                    ]
                )
                fallback.append(
                    row["title"]
                    + "  "
                    + clock(row["duration_seconds"])
                    + "  "
                    + str(row.get("bpm") or "—")
                )
        for table_row in cells:
            for column, cell in enumerate(table_row):
                cell["align"] = "left" if column == 0 else "center"
                cell["valign"] = "top"
        response.articles = [
            ArticlePage(
                blocks=[
                    {"type": "paragraph", "text": response.text},
                    {
                        "type": "table",
                        "cells": cells,
                        "is_bordered": True,
                        "is_striped": True,
                        "is_compact": True,
                    },
                ],
                text=response.text + "\n\n" + "\n".join(fallback),
                buttons=row_buttons,
            )
        ]
        response.articles_keyboard = True
        if page:
            response.buttons.append((t("music_prev"), f"mi_page {job.id.hex} {page - 1}"))
        if page + 1 < pages:
            response.buttons.append((t("music_next"), f"mi_page {job.id.hex} {page + 1}"))
        if choosing:
            response.buttons.append((t("music_continue"), f"mi_preview {job.id.hex}"))
        else:
            if job.data["new_count"]:
                response.buttons.append(
                    (t("music_commit"), f"mi_commit {job.id.hex} {job.data['preview_token']}")
                )
            response.buttons.append((t("music_back"), f"mi_back {job.id.hex}"))
        response.buttons.append(cancel)
    elif job.status == "done":
        response.text = t("music_done", count=job.data.get("imported_count", 0))
        response.buttons = methods(controller)
    elif job.status == "cancelled":
        response.text = t("music_cancelled")
    else:
        response.text = t("music_failed")
        response.buttons = [(t("music_entry"), f"mi_open {job.group_id.hex}")]
    return response


async def handle_music(controller: Controller, command: str, args: str) -> Response:
    t = controller.t
    if command == "mi_help":
        require(args in ("manual", "sheets", "telegram", "methods"), "input")
        return Response(
            t("music_help_intro" if args == "methods" else "music_help_" + args),
            methods(controller) if args == "methods" else [(t("music_methods"), "mi_help methods")],
        )
    if command == "start":
        if args.startswith("musicjob_"):
            require(controller.private, "private_only")
            service = MusicSearches(controller.store, controller.user, controller.user.telegram_id)
            job = await service.load(UUID(args.removeprefix("musicjob_")))
            return await music_view(controller, job)
        command, args = "mi_open", args.removeprefix("music_")
    if command == "mi_open":
        group_id = UUID(args)
        group = await controller.groups.access(group_id, admin=True)
        if not controller.private:
            require(
                bool(
                    await controller.store.find(
                        ChatBinding, chat_id=controller.chat_id, group_id=group_id
                    )
                ),
                "not_found",
            )
            controller.language = group.language
        row = Collectives(controller.store, controller.user, controller.chat_id).session(
            "music_name",
            {"group_id": str(group.id), "group_epoch": group.lifecycle_version, "name": group.name},
            minutes=10,
        )
        return Response(
            t("music_name_confirm", name=group.name),
            [
                (t("music_name_yes"), f"mi_name_yes {row.id.hex}"),
                (t("music_name_manual"), f"mi_name_manual {row.id.hex}"),
                (t("cancel_button"), f"mi_name_cancel {row.id.hex}"),
            ],
            workflow_id=row.id,
        )
    if command.startswith("mi_name_"):
        require(command in ("mi_name_yes", "mi_name_manual", "mi_name_cancel"), "input")
        row, data = await Collectives(controller.store, controller.user, controller.chat_id).load(
            UUID(args), "music_name"
        )
        require(
            row.user_id == controller.user.id and row.chat_id == controller.chat_id, "input_expired"
        )
        group = await controller.groups.access(UUID(data["group_id"]), admin=True)
        require(group.lifecycle_version == data["group_epoch"], "input_expired")
        if not controller.private:
            require(
                bool(
                    await controller.store.find(
                        ChatBinding, chat_id=controller.chat_id, group_id=group.id
                    )
                ),
                "input_expired",
            )
        await controller.store.delete(row)
        if command == "mi_name_cancel":
            return Response(t("music_not_started"))
        context = await controller.groups.context(controller.chat_id, controller.private)
        context.group_id, context.concert_id, context.selected_song_id = group.id, None, None
        if command == "mi_name_manual":
            query_input = await controller.inputs.create("music_query", context, t("music_query"))
            return Response(t("music_query"), force_reply=True, command_input_id=query_input.id)
        service = MusicSearches(controller.store, controller.user, controller.user.telegram_id)
        job = await service.create(
            group.id,
            data["name"],
            origin_chat_id=controller.chat_id if not controller.private else None,
        )
        return await music_view(controller, job)
    service = MusicSearches(controller.store, controller.user, controller.user.telegram_id)
    if command == "music_query":
        query_target(args)
        context = await controller.groups.context(controller.chat_id, controller.private)
        require(context.group_id is not None, "select_group")
        assert context.group_id is not None
        job = await service.create(
            context.group_id,
            args,
            origin_chat_id=controller.chat_id if not controller.private else None,
        )
        return await music_view(controller, job)
    values = args.split(maxsplit=2)
    job = await service.load(UUID(values[0]))
    if not controller.private:
        require(
            job.data.get("origin_chat_id") == controller.chat_id
            and bool(
                await controller.store.find(
                    ChatBinding, group_id=job.group_id, chat_id=controller.chat_id
                )
            ),
            "input_expired",
        )
        require(command in ("mi_artist", "mi_cancel", "mi_view"), "private_only")
        if job.status in ("failed", "cancelled"):
            return await music_view(controller, job)
        if job.status not in ("searching", "artists"):
            return Response(
                t("music_open_private"),
                [
                    (
                        t("music_open_results"),
                        f"https://t.me/{controller.bot_username}?start=musicjob_{job.id.hex}",
                    )
                ],
            )
    if command == "mi_cancel":
        require(job.status in ACTIVE, "already_done")
        job.status = "cancelled"
        service.write(job)
    elif command == "mi_artist":
        await service.choose_artist(job, int(values[1]))
    elif command == "mi_toggle":
        service.toggle(job, int(values[1]))
        await notify_search(controller.store, job, debounce=True)
        return Response("", deferred=True)
    elif command == "mi_page":
        require(job.status in ("choosing", "preview"), "input_expired")
        count = len(job.data["tracks"] if job.status == "choosing" else job.data["preview_rows"])
        require(0 <= int(values[1]) < max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE), "input")
        service.write(job, page=int(values[1]))
        await notify_search(controller.store, job, debounce=True)
        return Response("", deferred=True)
    elif command == "mi_preview":
        require(job.status == "choosing", "input_expired")
        selected = service.selected(job)
        missing = next(((index, row) for index, row in selected if row["seconds"] is None), None)
        if missing:
            index, track = missing
            prompt = t("music_duration_prompt", title=track["title"])
            context = await controller.groups.context(controller.chat_id, True)
            context.group_id = job.group_id
            row = await controller.inputs.create(
                "music_duration", context, prompt, prefix=f"{job.id.hex} {index}"
            )
            return Response(prompt, force_reply=True, command_input_id=row.id)
        await service.preview(job)
    elif command == "music_duration":
        require(job.status == "choosing", "input_expired")
        index = int(values[1])
        require(0 <= index < len(job.data["tracks"]), "input")
        seconds = duration(values[2])
        service.write(job, durations={**job.data.get("durations", {}), str(index): seconds})
        return await handle_music(controller, "mi_preview", job.id.hex)
    elif command == "mi_back":
        require(job.status == "preview", "input_expired")
        pending = await controller.store.get(CatalogImport, UUID(job.data["import_id"]))
        pending.status = "cancelled"
        job.status = "choosing"
        service.write(job, page=0, preview_token="")
    elif command == "mi_commit":
        await service.commit(job, values[1])
    else:
        require(command == "mi_view", "input")
    return await music_view(controller, job)
