"""Bounded, best-effort UX buffer. Business transactions never depend on analytics.

RU: События не содержат аргументов команд. В памяти не более 1000 записей;
при сбое запись повторяется, переполнение учитывается счётчиком, но не тормозит бота.
EN: Analytics is deliberately lossy on process crashes, never a business audit log.
"""

import asyncio
import hashlib
from collections import Counter, deque
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.application.ux import Trace
from setlist_bot.domain.entities import now
from setlist_bot.infrastructure.database import metadata
from setlist_bot.infrastructure.runtime import build_revision
from setlist_bot.infrastructure.telemetry import Monitor

# Explicit vocabulary prevents user-entered text, tokens and identifiers becoming labels.
ACTIONS = frozenset(
    (
        "support",
        "groups",
        "concerts",
        "all_my_concerts",
        "calendar",
        "cal_scope",
        "cal_create",
        "cal_links",
        "cal_change",
        "cal_confirm",
        "cal_cancel",
        "cal_help",
        "my_concert",
        "start",
        "help",
        "group",
        "group_create_yes",
        "group_create_edit",
        "group_create_cancel",
        "register",
        "catalog",
        "archived",
        "setlist",
        "shared",
        "export",
        "add",
        "suggestion",
        "versions",
        "history",
        "pause",
        "move",
        "remove",
        "edit",
        "copy",
        "alias",
        "publish",
        "cancel",
        "members",
        "proposals",
        "concert",
        "approve",
        "reopen",
        "reminders",
        "add_new_songs",
        "language",
        "group_language",
        "group_pause",
        "backup",
        "delete_concert",
        "delete_group",
        "trash",
        "use_group",
        "use_concert",
        "copy_list",
        "pick_start",
        "pick_text",
        "pick_confirm",
        "pick_cancel",
        "picker_pause",
        "import_commit",
        "import_cancel",
        "map_col",
        "confirm",
        "confirm_cancel",
        "song_card",
        "song_edit_menu",
        "save_song",
        "position",
        "position_cancel",
        "concert_create",
        "slmenu",
        "slact",
        "slreq",
        "slreturn",
        "sldraft",
        "slsettings",
        "slformat",
        "export_menu",
        "export_file",
        "export_settings",
        "mi_open",
        "mi_name_yes",
        "mi_name_manual",
        "mi_name_cancel",
        "mi_artist",
        "mi_toggle",
        "mi_page",
        "mi_preview",
        "mi_back",
        "mi_commit",
        "mi_cancel",
        "mi_view",
        "mi_help",
        "music_query",
        "music_duration",
        "ct_begin",
        "ct_code",
        "ct_yes",
        "ct_cancel",
        "chat_transfer",
        "cr_request",
        "cr_cancel",
        "cr_decide",
        "cleanup_chat",
        "input_cancel",
        "on_added",
    )
)
SUBACTIONS = frozenset(
    (
        "main",
        "options",
        "variants",
        "concerts",
        "approve",
        "reopen",
        "return",
        "cancel",
        "publish",
        "font",
        "size",
        "align",
        "fonts",
        "sizes",
        "alignment",
        "mono",
        "article",
        "toggle",
        "back",
        "yes",
        "no",
        "musician",
        "lighting",
        "all",
        "translit",
        "en",
        "ru",
    )
)


def action_name(value: str, callback: bool) -> str:
    parts = value.lstrip().split()
    name = parts[0].lstrip("/").split("@")[0].lower() if parts else ""
    if name not in ACTIONS:
        return "other_button" if callback else "text_input"
    if callback and name in {"slmenu", "slact", "slsettings", "slformat", "cr_decide", "sldraft"}:
        if len(parts) >= 3 and parts[2] in SUBACTIONS:
            name += "." + parts[2]
    return name


def pseudonym(value: UUID | None) -> str:
    return hashlib.sha256(("setlist-ux-v1:" + str(value)).encode()).hexdigest() if value else ""


class UXBuffer:
    def __init__(self, capacity: int = 1000):
        self.pending: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.dropped = 0
        self.next_cleanup = now()
        self.lock = asyncio.Lock()
        self.revision = build_revision()[:80]

    def add(self, trace: Trace, action: str, area: str, elapsed_ms: int) -> None:
        if trace.actor is None:
            return
        entries = [(action, trace.group, trace.resource)]
        if trace.outcome == "ok":
            entries.extend(trace.milestones)
        for name, group, resource in entries:
            if len(self.pending) == self.pending.maxlen:
                self.dropped += 1
            self.pending.append(
                {
                    "id": uuid4(),
                    "created_at": now(),
                    "action": name,
                    "outcome": trace.outcome,
                    "area": area,
                    "actor_key": pseudonym(trace.actor),
                    "group_key": pseudonym(group),
                    "resource_key": pseudonym(resource),
                    "elapsed_ms": min(max(elapsed_ms, 0), 3600000),
                    "revision": self.revision,
                }
            )

    async def flush(self, engine: AsyncEngine) -> None:
        async with self.lock:
            batch = list(self.pending)[:200]
            if not batch and now() < self.next_cleanup:
                return
            async with engine.begin() as connection:
                if batch:
                    table = metadata.tables["ux_events"]
                    inserted = await connection.execute(
                        insert(table).on_conflict_do_nothing().returning(*table.c), batch
                    )
                    new_rows = list(inserted.mappings())
                    counts: Counter[tuple[Any, ...]] = Counter()
                    durations: Counter[tuple[Any, ...]] = Counter()
                    for row in new_rows:
                        key = (
                            row["created_at"].astimezone(ZoneInfo("Europe/Moscow")).date(),
                            row["action"],
                            row["outcome"],
                            row["area"],
                        )
                        counts[key] += 1
                        durations[key] += row["elapsed_ms"]
                    daily = metadata.tables["ux_daily"]
                    values = [
                        {
                            "id": uuid4(),
                            "day": k[0],
                            "action": k[1],
                            "outcome": k[2],
                            "area": k[3],
                            "events": count,
                            "elapsed_ms": durations[k],
                        }
                        for k, count in counts.items()
                    ]
                    statement = insert(daily).values(values)
                    if values:
                        await connection.execute(
                            statement.on_conflict_do_update(
                                index_elements=["day", "action", "outcome", "area"],
                                set_={
                                    "events": daily.c.events + statement.excluded.events,
                                    "elapsed_ms": daily.c.elapsed_ms
                                    + statement.excluded.elapsed_ms,
                                },
                            )
                        )
                if now() >= self.next_cleanup:
                    # Bound each deletion transaction; a backlog is handled on subsequent ticks.
                    result = await connection.execute(
                        text(
                            "DELETE FROM ux_events WHERE id IN "
                            "(SELECT id FROM ux_events WHERE created_at < :cutoff LIMIT 10000)"
                        ),
                        {"cutoff": now() - timedelta(days=90)},
                    )
                    self.next_cleanup = now() + timedelta(
                        seconds=10 if result.rowcount == 10000 else 3600
                    )
            # No await between committing and removing the exact batch. New arrivals remain queued.
            consumed = {row["id"] for row in batch}
            self.pending = deque(
                (r for r in self.pending if r["id"] not in consumed), maxlen=self.pending.maxlen
            )

    async def run(self, engine: AsyncEngine, monitor: Monitor) -> None:
        while True:
            try:
                await self.flush(engine)
                monitor.set(
                    "ux_analytics",
                    "buffering",
                    success=True,
                )
                monitor.components["ux_analytics"]["details"].update(
                    buffered=len(self.pending), dropped=self.dropped
                )
            except Exception as exc:
                monitor.set("ux_analytics", "retrying", error=type(exc).__name__)
            await asyncio.sleep(10)
