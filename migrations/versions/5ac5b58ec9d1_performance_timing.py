"""Store event timing and the history of performance template choices."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "5ac5b58ec9d1"
down_revision = "cdd481792e03"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column(
        "concerts",
        sa.Column(
            "timing", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.add_column("concerts", sa.Column("template_source_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("concerts", "template_source_id")
    op.drop_column("concerts", "timing")
