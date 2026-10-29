"""Structural UX signals; no Telegram IDs, names, queries or message content."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class Trace:
    actor: UUID | None = None
    group: UUID | None = None
    resource: UUID | None = None
    outcome: str = "ok"
    action: str = ""
    milestones: list[tuple[str, UUID | None, UUID]] = field(default_factory=list)


current: ContextVar[Trace | None] = ContextVar("ux_trace", default=None)


def milestone(action: str, target: UUID, details: dict[str, Any]) -> None:
    trace = current.get()
    if action == "song_created" and details.get("group_id"):
        action, target = "catalog_populated", UUID(details["group_id"])
    if trace is None or action not in {
        "group_created",
        "setlist_created",
        "setlist_suggestion",
        "setlist_approved",
        "setlist_proposal_applied",
        "catalog_imported",
        "catalog_populated",
        "draft_saved",
        "variant_published",
        "pdf_requested",
    }:
        return
    group = UUID(details["group_id"]) if details.get("group_id") else target
    trace.group, trace.resource = group, target
    record = (action, group, target)
    if record not in trace.milestones:
        trace.milestones.append(record)
