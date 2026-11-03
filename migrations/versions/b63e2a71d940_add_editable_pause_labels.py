"""Persist optional pause labels; NULL keeps the localized legacy default."""

import sqlalchemy as sa
from alembic import op

revision = "b63e2a71d940"
down_revision = "a1426785384d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("items", sa.Column("pause_label", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_items_pause_label"),
        "items",
        "pause_label IS NULL OR (song_id IS NULL AND char_length(pause_label) <= 1000)",
    )


def downgrade():
    # RU: Не теряем подписи общих списков и не открываем личные черновики старому адаптеру.
    # EN: Refuse rollback while explicit labels exist, including deliberately blank labels.
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM items WHERE pause_label IS NOT NULL)")):
        raise RuntimeError("Downgrade would lose pause labels; preserve a backup.")
    for table, column in (
        ("personal_drafts", "content"),
        ("revisions", "snapshot"),
        ("submissions", "snapshot"),
    ):
        if bind.scalar(
            sa.text(
                f"SELECT EXISTS(SELECT 1 FROM {table} "
                f"WHERE jsonb_path_exists({column}, '$.**.pause_label'))"
            )
        ):
            raise RuntimeError("Downgrade would lose pause labels; preserve a backup.")
    op.drop_constraint(op.f("ck_items_pause_label"), "items", type_="check")
    op.drop_column("items", "pause_label")
