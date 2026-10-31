"""Personal read-only calendar subscriptions; no copies of business data."""

import sqlalchemy as sa
from alembic import op

revision = "b93e2a6f104c"
down_revision = "a82cd901be45"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE")),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "group_id", postgresql_nulls_not_distinct=True),
        sa.CheckConstraint("language IN ('ru', 'en')", name="language"),
        sa.CheckConstraint("version > 0", name="version"),
    )


def downgrade():
    # RU/EN: Do not silently invalidate calendars already connected on people's devices.
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM calendar_subscriptions WHERE enabled)")
    ).scalar():
        raise RuntimeError("Downgrade would lose active calendar subscriptions; disable them first")
    op.drop_table("calendar_subscriptions")
