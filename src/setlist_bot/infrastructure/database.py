from datetime import datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import registry

from setlist_bot.domain import entities as e
from setlist_bot.domain.rules import AppError

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)
mapper_registry = registry(metadata=metadata)


def col(name: str, kind: Any, *args: Any, **kwargs: Any) -> Column[Any]:
    return Column(name, kind, *args, nullable=kwargs.pop("nullable", False), **kwargs)


def tenant() -> Column[Any]:
    return col("group_id", Uuid, ForeignKey("groups.id"), index=True)


def table(entity: type[e.Entity], name: str, *columns: Any) -> None:
    mapper_registry.map_imperatively(
        entity,
        Table(
            name,
            metadata,
            col("id", Uuid, primary_key=True),
            *columns,
        ),
    )


table(
    e.User,
    "users",
    col("last_seen_at", DateTime(timezone=True), nullable=True, index=True),
    col("telegram_id", BigInteger, unique=True),
    col("language", String(2)),
    col("display_name", String(200)),
    col("export_settings", JSONB, server_default=text("'{}'::jsonb")),
)
table(
    e.Group,
    "groups",
    col("owner_id", Uuid, ForeignKey("users.id"), nullable=True),
    col("reminders_enabled", Boolean, server_default="true"),
    col("name", String(200)),
    col("language", String(2)),
    col("default_pause_seconds", Integer),
    col("deleted_at", DateTime(timezone=True), nullable=True, index=True),
    col("deleted_chat_id", BigInteger, nullable=True),
    col("lifecycle_version", Integer),
    CheckConstraint("default_pause_seconds BETWEEN 0 AND 86400", name="default_pause"),
)
table(
    e.Membership,
    "memberships",
    tenant(),
    col("user_id", Uuid, ForeignKey("users.id")),
    col("role", String(10)),
    col("musical_role", String(200)),
    col("status", String(10)),
    UniqueConstraint("group_id", "user_id"),
    CheckConstraint("role IN ('admin', 'member')", name="role"),
    CheckConstraint("status IN ('pending', 'active')", name="status"),
)
table(
    e.ChatBinding,
    "chat_bindings",
    col("group_id", Uuid, ForeignKey("groups.id"), unique=True),
    col("chat_id", BigInteger, unique=True),
)
table(
    e.Context,
    "contexts",
    col("user_id", Uuid, ForeignKey("users.id")),
    col("chat_id", BigInteger),
    col("group_id", Uuid, ForeignKey("groups.id"), nullable=True),
    col("concert_id", Uuid, nullable=True),
    col("observed_version", Integer, nullable=True),
    col("selected_song_id", Uuid, nullable=True),
    UniqueConstraint("user_id", "chat_id"),
)
table(
    e.Song,
    "songs",
    tenant(),
    col("created_at", DateTime(timezone=True), server_default=text("now()")),
    col("title", String(200)),
    col("duration_seconds", Integer),
    col("bpm", Integer, nullable=True),
    col("lights", Text),
    col("comment", Text),
    col("archived", Boolean),
    col("version", Integer),
    UniqueConstraint("group_id", "id"),
    CheckConstraint("duration_seconds > 0", name="duration"),
    CheckConstraint("bpm IS NULL OR (bpm > 0 AND bpm <= 1000)", name="bpm"),
)
table(
    e.SongAlias,
    "song_aliases",
    tenant(),
    col("song_id", Uuid),
    col("title", String(200)),
    UniqueConstraint("song_id", "title"),
    ForeignKeyConstraint(["group_id", "song_id"], ["songs.group_id", "songs.id"]),
)
table(
    e.CatalogProposal,
    "catalog_proposals",
    tenant(),
    col("author_id", Uuid, ForeignKey("users.id")),
    col("payload", JSONB),
    col("song_id", Uuid, nullable=True),
    col("expected_version", Integer, nullable=True),
    col("status", String(12)),
    ForeignKeyConstraint(["group_id", "song_id"], ["songs.group_id", "songs.id"]),
)
table(
    e.Concert,
    "concerts",
    tenant(),
    col("date", Date),
    col("title", String(200)),
    col("performance_duration_seconds", Integer, nullable=True),
    col("city", String(200), server_default=""),
    col("extra_info", Text, server_default=""),
    col("metadata_version", Integer, server_default="0"),
    CheckConstraint(
        "performance_duration_seconds > 0 AND performance_duration_seconds <= 86400",
        name="performance_duration",
    ),
    col("deleted_at", DateTime(timezone=True), nullable=True, index=True),
    col("lifecycle_version", Integer),
    UniqueConstraint("group_id", "id"),
)
table(
    e.Setlist,
    "setlists",
    tenant(),
    col("concert_id", Uuid, unique=True),
    col("default_pause_seconds", Integer),
    col("version", Integer),
    col("approved", Boolean),
    col("approved_revision_id", Uuid, nullable=True),
    UniqueConstraint("group_id", "id"),
    CheckConstraint("default_pause_seconds BETWEEN 0 AND 86400", name="default_pause"),
    ForeignKeyConstraint(["group_id", "concert_id"], ["concerts.group_id", "concerts.id"]),
    ForeignKeyConstraint(
        ["group_id", "approved_revision_id"],
        ["revisions.group_id", "revisions.id"],
        name="fk_approved_revision",
        use_alter=True,
    ),
)
table(
    e.Block,
    "blocks",
    tenant(),
    col("setlist_id", Uuid),
    col("position", Integer),
    col("title", String(200)),
    col("break_seconds", Integer),
    col("kind", String(10)),
    CheckConstraint("kind IN ('main', 'regular', 'encore')", name="kind"),
    UniqueConstraint("group_id", "id"),
    ForeignKeyConstraint(["group_id", "setlist_id"], ["setlists.group_id", "setlists.id"]),
    UniqueConstraint("setlist_id", "position", deferrable=True, initially="DEFERRED"),
    CheckConstraint("break_seconds >= 0", name="break"),
)
table(
    e.Item,
    "items",
    tenant(),
    col("block_id", Uuid),
    col("song_id", Uuid, nullable=True),
    col("position", Integer),
    col("pause_before", Integer, nullable=True),
    col("pause_duration_seconds", Integer, nullable=True),
    ForeignKeyConstraint(["group_id", "block_id"], ["blocks.group_id", "blocks.id"]),
    ForeignKeyConstraint(["group_id", "song_id"], ["songs.group_id", "songs.id"]),
    UniqueConstraint("block_id", "position", deferrable=True, initially="DEFERRED"),
    CheckConstraint("pause_before >= 0", name="pause"),
    CheckConstraint(
        "(song_id IS NOT NULL AND pause_duration_seconds IS NULL) OR "
        "(song_id IS NULL AND pause_duration_seconds IS NOT NULL AND "
        "pause_duration_seconds > 0 AND pause_duration_seconds <= 86400)",
        name="entry_kind",
    ),
)
table(
    e.Revision,
    "revisions",
    tenant(),
    col("setlist_id", Uuid),
    col("author_id", Uuid, ForeignKey("users.id")),
    col("number", Integer),
    col("action", String(100)),
    col("snapshot", JSONB),
    col("suggestion_number", Integer, nullable=True),
    col("proposer_id", Uuid, ForeignKey("users.id"), nullable=True),
    col("created_at", DateTime(timezone=True)),
    UniqueConstraint("setlist_id", "number"),
    UniqueConstraint(
        "setlist_id", "proposer_id", "suggestion_number", name="uq_revisions_proposer_variant"
    ),
    CheckConstraint(
        "(suggestion_number IS NULL AND proposer_id IS NULL) OR "
        "(suggestion_number IS NOT NULL AND suggestion_number > 0 AND proposer_id IS NOT NULL)",
        name="suggestion_number",
    ),
    UniqueConstraint("group_id", "id"),
    ForeignKeyConstraint(["group_id", "setlist_id"], ["setlists.group_id", "setlists.id"]),
)
table(
    e.PersonalDraft,
    "personal_drafts",
    tenant(),
    col("concert_id", Uuid),
    col("owner_id", Uuid, ForeignKey("users.id")),
    col("content", JSONB),
    col("version", Integer),
    UniqueConstraint("concert_id", "owner_id"),
    ForeignKeyConstraint(["group_id", "concert_id"], ["concerts.group_id", "concerts.id"]),
)
table(
    e.Submission,
    "submissions",
    tenant(),
    col("concert_id", Uuid),
    col("author_id", Uuid, ForeignKey("users.id")),
    col("snapshot", JSONB),
    col("applied", Boolean),
    ForeignKeyConstraint(["group_id", "concert_id"], ["concerts.group_id", "concerts.id"]),
)
table(
    e.Outbox,
    "outbox",
    col("actor", String(100), server_default=""),
    col("source", String(20), server_default="system"),
    col("payload", JSONB, server_default=text("'{}'::jsonb")),
    col("support_request_id", Uuid, ForeignKey("outbox.id"), nullable=True, index=True),
    col("created_at", DateTime(timezone=True), nullable=True, index=True),
    col("delivered_at", DateTime(timezone=True), nullable=True),
    col("discarded_at", DateTime(timezone=True), nullable=True),
    col("attempts", Integer, server_default="0"),
    col("last_error", String(120), server_default=""),
    col("next_attempt_at", DateTime(timezone=True), nullable=True, index=True),
    col("support_kind", String(10), server_default=""),
    col("chat_id", BigInteger),
    col("text", Text),
    col("sent", Boolean),
    col("monospace", Boolean, server_default=text("false")),
    col("monospace_offset", Integer, server_default="0"),
    col("buttons", JSONB),
    col("group_id", Uuid, ForeignKey("groups.id"), nullable=True),
    col("concert_id", Uuid, nullable=True),
    ForeignKeyConstraint(["group_id", "concert_id"], ["concerts.group_id", "concerts.id"]),
)
table(
    e.SongListTemplate,
    "song_list_templates",
    tenant(),
    col("concert_id", Uuid),
    col("chat_id", BigInteger),
    col("titles", JSONB),
    col("expires_at", DateTime(timezone=True), index=True),
    col("owner_id", Uuid, ForeignKey("users.id"), nullable=True),
    col("version", Integer, nullable=True),
    col("group_epoch", Integer, server_default=text("0")),
    col("concert_epoch", Integer, server_default=text("0")),
    ForeignKeyConstraint(["group_id", "concert_id"], ["concerts.group_id", "concerts.id"]),
)
table(
    e.SetlistMessage,
    "setlist_messages",
    col("template_id", Uuid, ForeignKey("song_list_templates.id", ondelete="CASCADE"), index=True),
    col("chat_id", BigInteger),
    col("message_id", BigInteger),
    UniqueConstraint("chat_id", "message_id"),
)
table(
    e.ExportSnapshot,
    "export_snapshots",
    tenant(),
    col("concert_id", Uuid),
    col("chat_id", BigInteger),
    col("payload", JSONB),
    col("expires_at", DateTime(timezone=True), index=True),
    col("owner_id", Uuid, ForeignKey("users.id"), nullable=True),
    col("group_epoch", Integer),
    col("concert_epoch", Integer),
    ForeignKeyConstraint(["group_id", "concert_id"], ["concerts.group_id", "concerts.id"]),
)
table(
    e.ExportFile,
    "export_files",
    col("export_id", Uuid, ForeignKey("export_snapshots.id", ondelete="CASCADE"), index=True),
    col("audience", String(12)),
    col("language", String(2)),
    col("transliterate", Boolean),
    col("filename", String(180)),
    col("content", LargeBinary),
    UniqueConstraint("export_id", "audience", "language", "transliterate"),
    CheckConstraint("audience IN ('musicians', 'lights')", name="audience"),
    CheckConstraint("language IN ('ru', 'en')", name="language"),
)
table(
    e.CatalogImport,
    "catalog_imports",
    tenant(),
    col("owner_id", Uuid, ForeignKey("users.id")),
    col("chat_id", BigInteger),
    col("source", Text),
    col("expires_at", DateTime(timezone=True)),
    col("mapping", JSONB, nullable=True),
    col("has_header", Boolean, nullable=True),
    col("status", String(12)),
)
table(
    e.ProcessedUpdate,
    "processed_updates",
    col("update_id", BigInteger, unique=True),
    col("created_at", DateTime(timezone=True), nullable=True, index=True),
)
table(
    e.Confirmation,
    "confirmations",
    col("user_id", Uuid, ForeignKey("users.id")),
    col("chat_id", BigInteger),
    col("command", Text),
    col("expires_at", DateTime(timezone=True)),
)
Index(
    "ix_confirmations_chat_id_expires_at",
    metadata.tables["confirmations"].c.chat_id,
    metadata.tables["confirmations"].c.expires_at,
)


# RU: Глобальная поддержка отделена от коллективов; личные черновики сюда не попадают.
# EN: Bot-wide support is independent of tenants and never copies private drafts.
table(
    e.SupportSettings,
    "support_settings",
    col("key", String(20), unique=True),
    col("owner_telegram_id", BigInteger),
    col("chat_id", BigInteger),
)
table(
    e.SupportMessage,
    "support_messages",
    col("outbox_id", Uuid, ForeignKey("outbox.id"), index=True),
    col("user_id", Uuid, ForeignKey("users.id")),
    col("chat_id", BigInteger),
    col("message_id", BigInteger, nullable=True),
    UniqueConstraint("chat_id", "message_id"),
)


table(
    e.Reminder,
    "reminders",
    tenant(),
    col("concert_id", Uuid, ForeignKey("concerts.id")),
    col("day", Date),
    col("outbox_id", Uuid, ForeignKey("outbox.id"), unique=True),
    UniqueConstraint("concert_id", "day"),
)


table(
    e.AdminSession,
    "admin_sessions",
    col("token_hash", String(64), unique=True),
    col("actor", String(100)),
    col("created_at", DateTime(timezone=True)),
    col("expires_at", DateTime(timezone=True), index=True),
)
table(
    e.AdminLoginLimit,
    "admin_login_limits",
    col("key", String(64), unique=True),
    col("window_start", DateTime(timezone=True)),
    col("attempts", Integer),
)
table(
    e.AdminAudit,
    "admin_audit",
    col("source", String(20), server_default="web"),
    col("actor", String(100)),
    col("action", String(60)),
    col("target", String(100)),
    col("request_key", String(64), unique=True),
    col("created_at", DateTime(timezone=True), index=True),
    col("details", JSONB),
)
table(
    e.SupportCase,
    "support_cases",
    col("version", Integer, server_default="0"),
    col("last_reply_by", String(100), server_default=""),
    col("last_reply_source", String(20), server_default=""),
    col("request_id", Uuid, ForeignKey("outbox.id"), unique=True),
    col("status", String(12)),
    col("updated_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('open', 'closed')", name="status"),
)
table(
    e.RuntimeStatus,
    "runtime_status",
    col("last_success_at", DateTime(timezone=True), nullable=True),
    col("last_error_at", DateTime(timezone=True), nullable=True),
    col("details", JSONB, server_default=text("'{}'::jsonb")),
    col("component", String(40), unique=True),
    col("last_activity_at", DateTime(timezone=True), nullable=True),
    col("updated_at", DateTime(timezone=True)),
    col("state", String(40)),
    col("errors", Integer),
    col("last_error", String(120)),
    col("next_run", DateTime(timezone=True), nullable=True),
)
table(
    e.DeploymentIdentity,
    "deployment_identity",
    col("key", String(20), unique=True),
    col("bot_id", BigInteger),
    col("environment", String(20)),
)


class PostgresStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get[T: e.Entity](self, kind: type[T], entity_id: UUID, *, lock: bool = False) -> T:
        identity: Any = kind.id
        query = select(kind).where(identity == entity_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        result = (await self.session.scalars(query)).one_or_none()
        if result is None:
            raise AppError("not_found")
        return result

    async def get_many[T: e.Entity](self, kind: type[T], identifiers: set[UUID]) -> list[T]:
        # RU: Один SQL-запрос вместо запроса на каждую песню. Пустой набор не читает таблицу.
        # EN: Batch IDs in one SQL query; an empty set must never become a full-table scan.
        if not identifiers:
            return []
        identity: Any = kind.id
        return list(
            (await self.session.scalars(select(kind).where(identity.in_(identifiers)))).all()
        )

    async def find[T: e.Entity](self, kind: type[T], **filters: Any) -> list[T]:
        query = select(kind)
        for key, value in filters.items():
            query = query.where(getattr(kind, key) == value)
        return list((await self.session.scalars(query)).all())

    def add(self, entity: e.Entity) -> None:
        self.session.add(entity)

    async def expired[T: e.Entity](self, kind: type[T], field: str, before: datetime) -> list[T]:
        return list(
            (await self.session.scalars(select(kind).where(getattr(kind, field) <= before))).all()
        )

    async def lock_chat(self, chat_id: int) -> None:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": chat_id})

    async def delete(self, entity: e.Entity) -> None:
        await self.session.delete(entity)

    async def flush(self) -> None:
        await self.session.flush()


class PostgresUnitOfWork:
    def __init__(self, engine: AsyncEngine) -> None:
        self.session = async_sessionmaker(engine, expire_on_commit=False)()
        self.store = PostgresStore(self.session)

    async def __aenter__(self) -> "PostgresUnitOfWork":
        await self.session.begin()
        return self

    async def __aexit__(
        self,
        typ: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # RU: Unit of Work — граница одной операции пользователя: без ошибки сохраняем
        # всё вместе; при любой ошибке откатываем. finally возвращает соединение в пул.
        # EN: The unit of work commits one complete use case, or rolls it all back.
        # Always close the session so failures cannot exhaust the connection pool.
        try:
            if typ is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
