"""Durable internet discovery and a bounded public metadata cache."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e62f4a91b830"
down_revision = "d89c3e17a024"
branch_labels = None
depends_on = None


def upgrade():
    # RU: Новые таблицы не переписывают песни, черновики или историю выступлений.
    # EN: Add queue/cache storage without touching existing musical data.
    op.create_table(
        "music_searches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_epoch", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(300), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(40), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_music_searches_group_id", "music_searches", ["group_id"])
    op.create_index("ix_music_searches_expires_at", "music_searches", ["expires_at"])
    op.create_index(
        "ix_music_searches_due",
        "music_searches",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'searching'"),
    )
    op.create_table(
        "music_cache",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_music_cache_expires_at", "music_cache", ["expires_at"])


def downgrade():
    # RU/EN: Removing this feature must not silently discard a user's pending selection.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM music_searches
                 WHERE status NOT IN ('done', 'cancelled', 'failed')) THEN
        RAISE EXCEPTION 'downgrade would lose pending music searches';
      END IF;
    END $$""")
    op.drop_table("music_cache")
    op.drop_table("music_searches")
