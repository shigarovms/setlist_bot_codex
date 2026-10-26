"""Persistent support destination and reply routing."""

import sqlalchemy as sa
from alembic import op

revision = "e42b8c190a76"
down_revision = "d31a7b920e64"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "outbox", sa.Column("support_kind", sa.String(10), nullable=False, server_default="")
    )
    op.create_table(
        "support_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(20), nullable=False),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("outbox_id", sa.Uuid(), sa.ForeignKey("outbox.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("chat_id", "message_id"),
    )

    op.create_index("ix_support_messages_outbox_id", "support_messages", ["outbox_id"])


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM support_settings) "
                "OR EXISTS(SELECT 1 FROM support_messages) "
                "OR EXISTS(SELECT 1 FROM outbox WHERE support_kind <> '' AND NOT sent)"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError("Downgrade would lose data: support configuration or correspondence")
    op.drop_table("support_messages")
    op.drop_table("support_settings")
    op.drop_column("outbox", "support_kind")
