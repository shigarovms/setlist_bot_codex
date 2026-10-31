"""Article default; retain an explicitly selected monospace preference."""

import sqlalchemy as sa
from alembic import op

revision = "f2d7c614a083"
down_revision = "c04f7a92d531"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("groups", "setlist_style", server_default="article")
    # RU: Не затрагиваем коллективы, у которых зафиксирован ручной выбор mono.
    # EN: Only migrate the historical implicit default, not an audited preference.
    op.execute(
        sa.text(
            "UPDATE groups SET setlist_style='article' WHERE setlist_style='mono' "
            "AND NOT EXISTS (SELECT 1 FROM admin_audit a WHERE a.target=groups.id::text "
            "AND a.action='group_setlist_settings' AND a.details->>'style'='mono')"
        )
    )


def downgrade():
    # Existing group preferences survive rollback; only change the creation default.
    op.alter_column("groups", "setlist_style", server_default="mono")
