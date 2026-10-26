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
    col("telegram_id", BigInteger, unique=True),
    col("language", String(2)),
    col("display_name", String(200)),
)
table(
    e.Group,
    "groups",
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
    col("chat_id", BigInteger),
    col("text", Text),
    col("sent", Boolean),
    col("monospace", Boolean, server_default=text("false")),
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
table(e.ProcessedUpdate, "processed_updates", col("update_id", BigInteger, unique=True))
table(
    e.Confirmation,
    "confirmations",
    col("user_id", Uuid, ForeignKey("users.id")),
    col("chat_id", BigInteger),
    col("command", Text),
    col("expires_at", DateTime(timezone=True)),
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
        try:
            if typ is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
