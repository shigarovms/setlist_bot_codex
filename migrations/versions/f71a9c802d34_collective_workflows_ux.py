"""Collective-wide UI settings and pseudonymous UX measurements."""

import sqlalchemy as sa
from alembic import op

revision = "f71a9c802d34"
down_revision = "e62f4a91b830"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "groups", sa.Column("setlist_style", sa.String(10), nullable=False, server_default="mono")
    )
    op.add_column(
        "groups",
        sa.Column(
            "admins_only_suggestions", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_check_constraint("setlist_style", "groups", "setlist_style IN ('mono', 'article')")
    op.create_table(
        "ux_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(80), nullable=False),
        sa.Column("area", sa.String(20), nullable=False),
        sa.Column("actor_key", sa.String(64), nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column("resource_key", sa.String(64), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("revision", sa.String(80), nullable=False),
    )
    op.create_index("ix_ux_events_created_at", "ux_events", ["created_at"])
    op.create_index("ix_ux_events_resource", "ux_events", ["resource_key", "created_at"])
    op.create_table(
        "ux_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(80), nullable=False),
        sa.Column("area", sa.String(20), nullable=False),
        sa.Column("events", sa.BigInteger(), nullable=False),
        sa.Column("elapsed_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("day", "action", "outcome", "area"),
    )
    op.create_index("ix_ux_daily_day", "ux_daily", ["day"])


def downgrade():
    op.drop_table("ux_daily")
    op.drop_table("ux_events")
    op.drop_constraint(op.f("ck_groups_setlist_style"), "groups", type_="check")
    op.drop_column("groups", "admins_only_suggestions")
    op.drop_column("groups", "setlist_style")
