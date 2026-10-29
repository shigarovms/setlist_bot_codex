"""Optional performance type; existing performances retain an empty value."""

import sqlalchemy as sa
from alembic import op

revision = "d89c3e17a024"
down_revision = "c14f802d9a63"
branch_labels = None
depends_on = None


def upgrade():
    # RU: Добавляем только поле; репертуар и версии сет-листов не переписываются.
    # EN: Add metadata only, preserving all existing catalog and setlist history.
    op.add_column(
        "concerts",
        sa.Column("performance_type", sa.String(200), nullable=False, server_default=""),
    )


def downgrade():
    # RU/EN: Не допускаем незаметной потери введённых типов / refuse silent data loss.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM concerts WHERE performance_type <> '') THEN
        RAISE EXCEPTION 'downgrade would lose data: performance types';
      END IF;
    END $$""")
    op.drop_column("concerts", "performance_type")
