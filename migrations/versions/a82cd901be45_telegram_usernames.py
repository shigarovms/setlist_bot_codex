"""Observed Telegram handles for administration; roles still bind numeric IDs."""

import sqlalchemy as sa
from alembic import op

revision = "a82cd901be45"
down_revision = "f71a9c802d34"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("username", sa.String(32), nullable=False, server_default=""))
    op.add_column("users", sa.Column("username_seen_at", sa.DateTime(timezone=True)))
    op.create_index("ix_users_username", "users", ["username"])


def downgrade():
    op.drop_index("ix_users_username", "users")
    op.drop_column("users", "username_seen_at")
    op.drop_column("users", "username")
