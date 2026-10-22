"""Frozen export snapshots and cached PDF files.

Revision ID: 7c9e02d6a415
Revises: 8d4e6af920b1
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7c9e02d6a415"
down_revision = "8d4e6af920b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "export_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("concert_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("group_epoch", sa.Integer(), nullable=False),
        sa.Column("concert_epoch", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], name=op.f("fk_export_snapshots_group_id_groups")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_export_snapshots_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "concert_id"],
            ["concerts.group_id", "concerts.id"],
            name=op.f("fk_export_snapshots_group_id_concerts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_snapshots")),
    )
    op.create_index(op.f("ix_export_snapshots_group_id"), "export_snapshots", ["group_id"])
    op.create_index(op.f("ix_export_snapshots_expires_at"), "export_snapshots", ["expires_at"])
    op.create_table(
        "export_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("export_id", sa.Uuid(), nullable=False),
        sa.Column("audience", sa.String(12), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("transliterate", sa.Boolean(), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["export_id"],
            ["export_snapshots.id"],
            ondelete="CASCADE",
            name=op.f("fk_export_files_export_id_export_snapshots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_files")),
        sa.UniqueConstraint(
            "export_id",
            "audience",
            "language",
            "transliterate",
            name=op.f("uq_export_files_export_id"),
        ),
        sa.CheckConstraint(
            "audience IN ('musicians', 'lights')", name=op.f("ck_export_files_audience")
        ),
        sa.CheckConstraint("language IN ('ru', 'en')", name=op.f("ck_export_files_language")),
    )
    op.create_index(op.f("ix_export_files_export_id"), "export_files", ["export_id"])


def downgrade():
    op.drop_table("export_files")
    op.drop_table("export_snapshots")
