"""Web administration, audit, runtime status and observable outbox delivery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a924c61d08be"
down_revision = "f8129a4b6c30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"])
    op.add_column("outbox", sa.Column("support_request_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_outbox_support_request_id_outbox"),
        "outbox",
        "outbox",
        ["support_request_id"],
        ["id"],
    )
    op.create_index("ix_outbox_support_request_id", "outbox", ["support_request_id"])
    # RU/EN: Историческое время неизвестно / never invent timestamps for legacy rows.
    for table in ("processed_updates", "outbox"):
        op.add_column(table, sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])
    for name in ("delivered_at", "discarded_at", "next_attempt_at"):
        op.add_column("outbox", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "outbox", sa.Column("last_error", sa.String(120), nullable=False, server_default="")
    )
    op.create_index("ix_outbox_next_attempt_at", "outbox", ["next_attempt_at"])
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("token_hash"),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.create_table(
        "admin_login_limits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.UniqueConstraint("key"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target", sa.String(100), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.UniqueConstraint("request_key"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", JSONB(), nullable=False),
    )
    op.create_index("ix_admin_audit_created_at", "admin_audit", ["created_at"])
    op.create_table(
        "support_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("outbox.id"), nullable=False),
        sa.UniqueConstraint("request_id"),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_support_cases_status")),
    )
    op.create_table(
        "runtime_status",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("component", sa.String(40), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("component"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(120), nullable=False),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM admin_audit) OR EXISTS(SELECT 1 FROM support_cases) "
                "OR EXISTS(SELECT 1 FROM outbox WHERE support_request_id IS NOT NULL "
                "OR (NOT sent AND next_attempt_at IS NOT NULL))"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError("Downgrade would lose data: administration audit or support cases")
    for table in (
        "runtime_status",
        "support_cases",
        "admin_audit",
        "admin_login_limits",
        "admin_sessions",
    ):
        op.drop_table(table)
    op.drop_index("ix_users_last_seen_at", "users")
    op.drop_column("users", "last_seen_at")
    op.drop_index("ix_outbox_support_request_id", "outbox")
    op.drop_constraint(op.f("fk_outbox_support_request_id_outbox"), "outbox", type_="foreignkey")
    op.drop_column("outbox", "support_request_id")
    op.drop_index("ix_outbox_next_attempt_at", "outbox")
    for name in ("attempts", "last_error", "delivered_at", "discarded_at", "next_attempt_at"):
        op.drop_column("outbox", name)
    for table in ("processed_updates", "outbox"):
        op.drop_index(f"ix_{table}_created_at", table)
        op.drop_column(table, "created_at")
