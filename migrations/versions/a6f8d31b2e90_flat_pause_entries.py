"""Allow explicit pause entries without creating catalog songs."""

import sqlalchemy as sa
from alembic import op

revision = "a6f8d31b2e90"
down_revision = "7c9e02d6a415"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("items", "song_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("items", sa.Column("pause_duration_seconds", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_items_entry_kind"),
        "items",
        "(song_id IS NOT NULL AND pause_duration_seconds IS NULL) OR "
        "(song_id IS NULL AND pause_duration_seconds IS NOT NULL AND "
        "pause_duration_seconds > 0 AND pause_duration_seconds <= 86400)",
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM items WHERE song_id IS NULL)")
    ).scalar_one():
        raise RuntimeError("Pause entries exist; downgrade would lose data")
    for table, field in (
        ("personal_drafts", "content"),
        ("revisions", "snapshot"),
        ("submissions", "snapshot"),
        ("export_snapshots", "payload->'snapshot'"),
    ):
        if connection.execute(
            sa.text(
                f"SELECT EXISTS(SELECT 1 FROM {table} WHERE jsonb_path_exists({field}, "
                "'$.blocks[*].items[*] ? (@.kind == \"pause\")'))"
            )
        ).scalar_one():
            raise RuntimeError("JSON pause entries exist; downgrade would lose data")
    if connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM confirmations WHERE "
            "jsonb_path_exists(command::jsonb, '$.selected[*].pause_seconds'))"
        )
    ).scalar_one():
        raise RuntimeError("Pending pause selections exist; downgrade would lose data")
    op.drop_constraint(op.f("ck_items_entry_kind"), "items", type_="check")
    op.drop_column("items", "pause_duration_seconds")
    op.alter_column("items", "song_id", existing_type=sa.Uuid(), nullable=False)
