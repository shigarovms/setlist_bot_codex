"""Recoverable encrypted calendar secrets; existing hash-only links remain valid."""

import sqlalchemy as sa
from alembic import op

revision = "c04f7a92d531"
down_revision = "b93e2a6f104c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "calendar_subscriptions",
        sa.Column("token_ciphertext", sa.Text(), nullable=False, server_default=""),
    )


def downgrade():
    # RU/EN: Losing encrypted secrets would break redisplay. Do not silently erase them.
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM calendar_subscriptions WHERE token_ciphertext <> '')"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Downgrade would lose stored calendar links")
    op.drop_column("calendar_subscriptions", "token_ciphertext")
