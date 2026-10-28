"""Read models for paginated admin pages. Never select PersonalDraft.content or PDF bytes."""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

PAGE_SIZE = 25


class AdminQueries:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            return [dict(row) for row in (await connection.execute(text(sql), params)).mappings()]

    async def one(self, sql: str, **params: Any) -> dict[str, Any]:
        rows = await self.rows(sql, **params)
        return rows[0] if rows else {}

    async def overview(self) -> dict[str, Any]:
        return await self.one(
            """SELECT
          (SELECT count(*) FROM users) AS users,
          (SELECT count(*) FROM groups WHERE deleted_at IS NULL) AS groups,
          (SELECT count(*) FROM concerts WHERE deleted_at IS NULL AND date >= :today)
            AS concerts,
          (SELECT count(*) FROM outbox WHERE NOT sent) AS pending,
          (SELECT count(*) FROM outbox WHERE NOT sent AND last_error <> '') AS failed,
          (SELECT count(*) FROM processed_updates WHERE created_at >= now()-interval
            '24 hours') AS updates,
          (SELECT count(*) FROM outbox WHERE delivered_at >= now()-interval '24
            hours') AS deliveries,
          (SELECT count(*) FROM personal_drafts) AS drafts,
          (SELECT count(*) FROM outbox o LEFT JOIN support_cases c ON c.request_id=o.id
             WHERE o.support_kind='request' AND coalesce(c.status,'open')='open') AS support,
          pg_database_size(current_database()) AS database_bytes,
          pg_total_relation_size('export_files') AS pdf_storage_bytes,
          (SELECT version_num FROM alembic_version LIMIT 1) AS migration,
          (SELECT numbackends FROM pg_stat_database WHERE datname=current_database())
            AS connections""",
            today=datetime.now(ZoneInfo("Europe/Moscow")).date(),
        )

    async def activity(self) -> list[dict[str, Any]]:
        return await self.rows("""SELECT date_trunc('hour', created_at) AS hour, count(*) AS total
          FROM processed_updates WHERE created_at>=now()-interval '24 hours'
          GROUP BY 1 ORDER BY 1""")

    async def listing(
        self,
        section: str,
        *,
        query: str = "",
        page: int = 0,
        state: str = "",
        group_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        # RU/EN: Только доверенные SQL-шаблоны / search, state and IDs are bound parameters.
        statements = {
            "users": """
            SELECT u.id,u.telegram_id,u.display_name,u.language,u.last_seen_at,
            (SELECT count(*) FROM memberships m WHERE m.user_id=u.id AND
            m.status='active') AS groups
            FROM users u WHERE (u.display_name ILIKE :q OR u.telegram_id::text ILIKE :q)
            ORDER BY u.telegram_id LIMIT :limit OFFSET :offset""",
            "groups": """
            SELECT g.id,g.name,g.deleted_at,g.reminders_enabled,u.display_name AS owner_name,
            (SELECT count(*) FROM memberships m WHERE m.group_id=g.id AND
            m.status='active') AS members,
            (SELECT count(*) FROM concerts c WHERE c.group_id=g.id AND c.deleted_at IS
            NULL) AS concerts
            FROM groups g LEFT JOIN users u ON u.id=g.owner_id
            WHERE g.name ILIKE :q AND (:state='' OR (:state='deleted')=(g.deleted_at
            IS NOT NULL))
            ORDER BY g.name,g.id LIMIT :limit OFFSET :offset""",
            "concerts": """
            SELECT c.id,c.date,c.title,c.city,c.deleted_at,g.name AS group_name,g.id AS group_id,
            s.approved,s.version FROM concerts c JOIN groups g ON g.id=c.group_id
            JOIN setlists s ON s.concert_id=c.id
            WHERE (c.title ILIKE :q OR g.name ILIKE :q OR c.city ILIKE :q)
              AND (CAST(:group_id AS uuid) IS NULL OR c.group_id=:group_id)
              AND (:state='' OR (:state='approved' AND s.approved) OR
            (:state='working' AND NOT s.approved)
                   OR (:state='deleted' AND c.deleted_at IS NOT NULL))
            ORDER BY c.date DESC,c.id LIMIT :limit OFFSET :offset""",
            "queue": """
            SELECT o.id,o.created_at,o.chat_id,o.support_kind,o.sent,o.attempts,o.last_error,
            o.next_attempt_at,o.delivered_at,o.discarded_at,left(o.text,220) AS preview
            FROM outbox o WHERE (o.text ILIKE :q OR o.chat_id::text ILIKE :q)
              AND (:state='all' OR (:state='processed' AND o.sent) OR (:state='failed'
            AND NOT o.sent AND o.last_error<>'') OR (:state IN ('','pending') AND NOT
            o.sent))
            ORDER BY o.created_at DESC NULLS LAST,o.id LIMIT :limit OFFSET :offset""",
            "support": """
            SELECT o.id,o.created_at,left(o.text,220) AS preview,
            coalesce(c.status,'open') AS status,
            u.display_name,u.telegram_id FROM outbox o
            LEFT JOIN support_cases c ON c.request_id=o.id
            JOIN users u ON u.telegram_id=(SELECT u2.telegram_id FROM support_messages m
                JOIN users u2 ON u2.id=m.user_id WHERE m.outbox_id=o.id LIMIT 1)
            WHERE o.support_kind='request' AND (o.text ILIKE :q OR u.telegram_id::text
            ILIKE :q)
              AND (:state='all' OR coalesce(c.status,'open')=CASE WHEN :state='' THEN
            'open' ELSE :state END)
            ORDER BY o.created_at DESC NULLS LAST,o.id LIMIT :limit OFFSET :offset""",
            "audit": """
            SELECT id,created_at,actor,action,target,details FROM admin_audit
            WHERE (actor ILIKE :q OR action ILIKE :q OR target ILIKE :q)
            ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset""",
            "catalog": """
            SELECT id,title,duration_seconds,bpm,lights,comment,archived FROM songs
            WHERE group_id=:group_id AND title ILIKE :q
            ORDER BY title,id LIMIT :limit OFFSET :offset""",
        }
        return await self.rows(
            statements[section],
            q="%" + query[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE + 1,
            state=state,
            group_id=group_id,
        )

    async def user(self, identifier: UUID) -> dict[str, Any]:
        return await self.one(
            "SELECT id,telegram_id,display_name,language,last_seen_at FROM users WHERE id=:id",
            id=identifier,
        )

    async def memberships(
        self, *, group: UUID | None = None, user: UUID | None = None, query: str = "", page: int = 0
    ) -> list[dict[str, Any]]:
        return await self.rows(
            """SELECT m.id,m.role,m.status,m.musical_role AS nickname,
            u.display_name,u.telegram_id,u.id AS user_id,g.name,g.id AS group_id,g.owner_id
            FROM memberships m JOIN users u ON u.id=m.user_id JOIN groups g ON g.id=m.group_id
            WHERE (CAST(:group AS uuid) IS NULL OR m.group_id=:group)
              AND (CAST(:user AS uuid) IS NULL OR m.user_id=:user)
            AND (m.musical_role ILIKE :q OR u.display_name ILIKE :q OR
            u.telegram_id::text ILIKE :q)
            ORDER BY g.name,u.display_name,m.id LIMIT :limit OFFSET :offset""",
            group=group,
            user=user,
            q="%" + query[:200] + "%",
            limit=PAGE_SIZE + 1,
            offset=page * PAGE_SIZE,
        )

    async def group(self, identifier: UUID) -> dict[str, Any]:
        return await self.one(
            """SELECT g.*,u.display_name AS owner_name,u.telegram_id AS owner_telegram_id,
            b.chat_id FROM groups g LEFT JOIN users u ON u.id=g.owner_id
            LEFT JOIN chat_bindings b ON b.group_id=g.id WHERE g.id=:id""",
            id=identifier,
        )

    async def concert(self, identifier: UUID) -> dict[str, Any]:
        return await self.one(
            """SELECT c.*,g.name AS group_name,s.id AS setlist_id,s.approved,s.version,
            s.approved_revision_id,s.default_pause_seconds FROM concerts c
            JOIN groups g ON g.id=c.group_id JOIN setlists s ON s.concert_id=c.id
            WHERE c.id=:id""",
            id=identifier,
        )

    async def shared_snapshot(self, concert: dict[str, Any]) -> dict[str, Any]:
        if concert["approved"] and concert["approved_revision_id"]:
            row = await self.one(
                "SELECT snapshot FROM revisions WHERE id=:id", id=concert["approved_revision_id"]
            )
            return dict(row["snapshot"])
        rows = await self.rows(
            """SELECT i.pause_duration_seconds,i.pause_before,s.id AS song_id,s.title,
            s.duration_seconds,s.bpm,s.lights,s.comment FROM items i
            JOIN blocks b ON b.id=i.block_id LEFT JOIN songs s ON s.id=i.song_id
            WHERE b.setlist_id=:id ORDER BY b.position,i.position""",
            id=concert["setlist_id"],
        )
        from setlist_bot.domain.rules import pause_item

        return {
            "default_pause_seconds": concert["default_pause_seconds"],
            "blocks": [
                {
                    "items": [
                        pause_item(row["pause_duration_seconds"])
                        if row["pause_duration_seconds"] is not None
                        else {**row, "song_id": str(row["song_id"])}
                        for row in rows
                    ]
                }
            ],
        }

    async def ticket(self, identifier: UUID) -> dict[str, Any]:
        return await self.one(
            """SELECT o.id,o.created_at,o.text,coalesce(c.status,'open') AS status
            FROM outbox o LEFT JOIN support_cases c ON c.request_id=o.id
            WHERE o.id=:id AND o.support_kind='request'""",
            id=identifier,
        )

    async def replies(self, identifier: UUID) -> list[dict[str, Any]]:
        return await self.rows(
            """SELECT id,text,created_at,delivered_at,discarded_at,last_error,sent FROM outbox
            WHERE support_request_id=:id ORDER BY created_at,id LIMIT 100""",
            id=identifier,
        )

    async def runtime(self) -> list[dict[str, Any]]:
        return await self.rows(
            "SELECT component,updated_at,last_activity_at,state,errors,last_error,next_run "
            "FROM runtime_status ORDER BY component"
        )
