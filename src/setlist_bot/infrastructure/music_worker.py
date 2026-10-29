"""RU: Очередь без долгих транзакций. EN: Lease a step, close DB, fetch, checkpoint.

At most one source request stream per bot process. Durable leases also protect a
manually invoked worker; final catalog writes never happen in this worker.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import aiohttp
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine

from setlist_bot.application.groups import Groups
from setlist_bot.application.music_search import notify_search
from setlist_bot.domain.entities import MusicCache, MusicSearch, User, now
from setlist_bot.domain.music_discovery import default_exclusions, merged_tracks
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork, metadata
from setlist_bot.infrastructure.music_sources import MusicSources, SourceError, skip_phase
from setlist_bot.infrastructure.telemetry import Monitor

logger = logging.getLogger(__name__)


class Sources(Protocol):
    async def step(self, data: dict[str, Any], query: str) -> tuple[dict[str, Any], str | None]: ...


async def search_one(engine: AsyncEngine, sources: Sources) -> bool:
    at = now()
    columns = metadata.tables["music_searches"].c
    async with PostgresUnitOfWork(engine) as uow:
        job = (
            await uow.session.scalars(
                select(MusicSearch)
                .where(
                    columns.status == "searching",
                    columns.expires_at > at,
                    columns.next_attempt_at <= at,
                    or_(columns.lease_until.is_(None), columns.lease_until <= at),
                )
                .order_by(columns.next_attempt_at, columns.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).first()
        if job is None:
            return False
        token = uuid4().hex
        job.lease_token, job.lease_until = token, at + timedelta(seconds=90)
        identifier, owner_id, group_id = job.id, job.owner_id, job.group_id
        state, query = json.loads(json.dumps(job.data)), job.query

    # RU/EN: No DB lock/connection is held while an external service is slow.
    outcome = None
    error: SourceError | None = None
    try:
        async with PostgresUnitOfWork(engine) as uow:
            owner = await uow.store.get(User, owner_id)
            group = await Groups(uow.store, owner).access(group_id, admin=True)
            current = await uow.store.get(MusicSearch, identifier)
            if group.lifecycle_version != current.group_epoch or current.status != "searching":
                raise AppError("input_expired")
        state, outcome = await sources.step(state, query)
    except SourceError as exc:
        error = exc
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError):
        # A changed website layout must degrade one provider, not crash polling.
        error = SourceError(502)
    except AppError:
        outcome = "cancelled"

    async with PostgresUnitOfWork(engine) as uow:
        job = await uow.store.get(MusicSearch, identifier, lock=True)
        if job.status != "searching" or job.lease_token != token:
            return True
        job.lease_until, job.lease_token = None, ""
        job.next_attempt_at = now() + timedelta(seconds=2)
        if outcome == "cancelled":
            job.status = "cancelled"
            job.version += 1
            return True
        if error:
            job.failures += 1
            phase = state["phase"]
            if error.status in (0, 429, 500, 502, 503, 504) and job.failures < 3:
                # Honor Retry-After; retries are bounded, persisted, and do not block the queue.
                job.next_attempt_at = now() + timedelta(
                    seconds=max(error.retry_after, 30 * job.failures)
                )
                return True
            state.setdefault("warnings", []).append(f"{phase}: unavailable ({error.status})")
            if phase == "resolve":
                outcome = "failed"
            else:
                state = skip_phase(state)
        job.failures = 0
        started_at = (
            datetime.fromisoformat(state["search_started_at"])
            if state.get("search_started_at")
            else job.created_at
        )
        if now() - started_at > timedelta(minutes=40) and state["phase"] not in (
            "resolve",
            "examples",
        ):
            state["phase"] = "finish"
            state.setdefault("warnings", []).append("Search time limit reached; partial results")
        if state["phase"] == "finish" or outcome == "choosing":
            state["tracks"] = merged_tracks(state.get("raw", []))
            state["excluded"] = default_exclusions(state["tracks"])
            # Keep only the useful source references in the result, not thousands of raw duplicates.
            for key in ("raw", "bpm_pending", "albums"):
                state.pop(key, None)
            outcome = "choosing" if state["tracks"] else "failed"
            if state["tracks"]:
                key = "public-v1:" + state["artist"]["id"]
                caches = await uow.store.find(MusicCache, key=key)
                payload = {"tracks": state["tracks"], "warnings": state.get("warnings", [])}
                if caches:
                    caches[0].payload, caches[0].expires_at = payload, now() + timedelta(hours=24)
                else:
                    uow.store.add(
                        MusicCache(key=key, payload=payload, expires_at=now() + timedelta(hours=24))
                    )
        # Preserve UI message bindings recorded while the HTTP request was in flight.
        state["panel_ids"] = job.data.get("panel_ids", [])
        state["group_panel_ids"] = job.data.get("group_panel_ids", [])
        job.data = state
        job.version += 1
        if outcome:
            job.status = outcome
            if outcome != "cancelled":
                await notify_search(uow.store, job)
    return True


async def search_worker(engine: AsyncEngine, monitor: Monitor | None = None) -> None:
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    headers = {"User-Agent": "SetlistBot/0.1 (+https://github.com/shigarovms/setlist_bot_codex)"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=True) as session:
        sources = MusicSources(session)
        while True:
            try:
                worked = await search_one(engine, sources)
                if monitor:
                    monitor.set("music_search", "working" if worked else "waiting", success=worked)
            except Exception as exc:
                # Provider URLs, queries, user content and exception messages stay out of logs.
                logger.warning("Music search step failed (%s)", type(exc).__name__)
                if monitor:
                    monitor.set("music_search", "retrying", error=type(exc).__name__)
            await asyncio.sleep(2)
