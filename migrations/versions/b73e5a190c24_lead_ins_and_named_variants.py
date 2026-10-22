"""Move pauses before songs and record variant authors.

Revision ID: b73e5a190c24
Revises: 92c725b48a61
"""

from copy import deepcopy

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "b73e5a190c24"
down_revision = "92c725b48a61"
branch_labels = None
depends_on = None


def upgrade():
    # Move each existing gap to the next song, within its block. Historical
    # snapshots remain byte-for-byte unchanged and are read through an adapter.
    op.execute(
        "WITH shifted AS (SELECT id, lag(pause_after) OVER "
        "(PARTITION BY block_id ORDER BY position) AS lead_in FROM items) "
        "UPDATE items SET pause_after = shifted.lead_in FROM shifted WHERE items.id = shifted.id"
    )
    op.alter_column("items", "pause_after", new_column_name="pause_before")
    op.add_column("revisions", sa.Column("suggestion_number", sa.Integer(), nullable=True))
    op.add_column("revisions", sa.Column("proposer_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_revisions_proposer_id_users"), "revisions", "users", ["proposer_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_revisions_proposer_variant",
        "revisions",
        ["setlist_id", "proposer_id", "suggestion_number"],
    )
    op.create_check_constraint(
        op.f("ck_revisions_suggestion_number"),
        "revisions",
        "(suggestion_number IS NULL AND proposer_id IS NULL) OR "
        "(suggestion_number IS NOT NULL AND suggestion_number > 0 AND proposer_id IS NOT NULL)",
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM items WHERE position = 1 AND pause_before > 0)")
    ):
        raise RuntimeError(
            "Cannot downgrade: the previous schema cannot represent a lead-in before the first song"
        )
    for name, column in (
        ("personal_drafts", "content"),
        ("revisions", "snapshot"),
        ("submissions", "snapshot"),
    ):
        document_table = sa.table(name, sa.column("id", sa.Uuid()), sa.column(column, JSONB()))
        for identifier, document in connection.execute(sa.select(document_table)).yield_per(100):
            legacy = deepcopy(document)
            for block in legacy["blocks"]:
                items = block["items"]
                if items and "pause_before" in items[0]:
                    if items[0]["pause_before"]:
                        raise RuntimeError(
                            "Cannot downgrade: stored snapshot has a lead-in before the first song"
                        )
                    for index, item in enumerate(items):
                        item["pause_after"] = (
                            items[index + 1]["pause_before"] if index + 1 < len(items) else None
                        )
                    for item in items:
                        item.pop("pause_before")
            if legacy != document:
                connection.execute(
                    document_table.update()
                    .where(document_table.c.id == identifier)
                    .values({column: legacy})
                )
    op.execute(
        "WITH shifted AS (SELECT id, lead(pause_before) OVER "
        "(PARTITION BY block_id ORDER BY position) AS gap FROM items) "
        "UPDATE items SET pause_before = shifted.gap FROM shifted WHERE items.id = shifted.id"
    )
    op.drop_constraint(op.f("ck_revisions_suggestion_number"), "revisions", type_="check")
    op.drop_constraint("uq_revisions_proposer_variant", "revisions", type_="unique")
    op.drop_constraint(op.f("fk_revisions_proposer_id_users"), "revisions", type_="foreignkey")
    op.drop_column("revisions", "proposer_id")
    op.drop_column("revisions", "suggestion_number")
    op.alter_column("items", "pause_before", new_column_name="pause_after")
