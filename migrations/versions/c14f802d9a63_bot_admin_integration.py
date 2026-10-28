"""Bot identity, shared support state, card refresh jobs and diagnostic timestamps."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c14f802d9a63"
down_revision = "a924c61d08be"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_identity",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(20), nullable=False),
        sa.UniqueConstraint("key"),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
    )
    op.add_column(
        "admin_audit", sa.Column("source", sa.String(20), nullable=False, server_default="web")
    )
    for name, length, default in (("actor", 100, ""), ("source", 20, "system")):
        op.add_column(
            "outbox", sa.Column(name, sa.String(length), nullable=False, server_default=default)
        )
    op.add_column(
        "outbox",
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "support_cases", sa.Column("version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "support_cases",
        sa.Column("last_reply_by", sa.String(100), nullable=False, server_default=""),
    )
    op.add_column(
        "support_cases",
        sa.Column("last_reply_source", sa.String(20), nullable=False, server_default=""),
    )
    for name in ("last_success_at", "last_error_at"):
        op.add_column("runtime_status", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "runtime_status",
        sa.Column("details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_confirmations_chat_id_expires_at", "confirmations", ["chat_id", "expires_at"]
    )


def downgrade():
    # RU: Старый worker не понимает задания обновления; нельзя потерять их при откате.
    # EN: Refuse a downgrade that would erase pending operations or a deployment binding.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM outbox WHERE NOT sent AND support_kind='refresh')
         OR EXISTS (SELECT 1 FROM deployment_identity)
         OR EXISTS (SELECT 1 FROM admin_audit WHERE source <> 'web')
         OR EXISTS (SELECT 1 FROM support_cases WHERE version <> 0) THEN
        RAISE EXCEPTION 'downgrade would lose data: bot/admin integration';
      END IF;
    END $$""")
    op.drop_index("ix_confirmations_chat_id_expires_at", table_name="confirmations")
    for name in ("details", "last_error_at", "last_success_at"):
        op.drop_column("runtime_status", name)
    for name in ("last_reply_source", "last_reply_by", "version"):
        op.drop_column("support_cases", name)
    for name in ("payload", "source", "actor"):
        op.drop_column("outbox", name)
    op.drop_column("admin_audit", "source")
    op.drop_table("deployment_identity")
