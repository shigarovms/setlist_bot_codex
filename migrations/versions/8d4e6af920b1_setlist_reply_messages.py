"""Remember working setlist messages and preserve monospace formatting in outbox.

Revision ID: 8d4e6af920b1
Revises: 3818f9aeed43
"""

import sqlalchemy as sa
from alembic import op

revision = "8d4e6af920b1"
down_revision = "3818f9aeed43"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "outbox", sa.Column("monospace", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("song_list_templates", sa.Column("version", sa.Integer(), nullable=True))
    for name in ("group_epoch", "concert_epoch"):
        op.add_column(
            "song_list_templates", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "setlist_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["song_list_templates.id"],
            ondelete="CASCADE",
            name=op.f("fk_setlist_messages_template_id_song_list_templates"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_setlist_messages")),
        sa.UniqueConstraint("chat_id", "message_id", name=op.f("uq_setlist_messages_chat_id")),
    )
    op.create_index(op.f("ix_setlist_messages_template_id"), "setlist_messages", ["template_id"])


def downgrade():
    op.drop_table("setlist_messages")
    for name in ("version", "group_epoch", "concert_epoch"):
        op.drop_column("song_list_templates", name)
    op.drop_column("outbox", "monospace")
