"""Add average pauses while preserving existing concert timing.

Revision ID: 92c725b48a61
Revises: 2f0007ae2d83
"""

from copy import deepcopy

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "92c725b48a61"
down_revision = "2f0007ae2d83"
branch_labels = None
depends_on = None


def upgrade():
    # Group defaults apply only to newly created concerts. Existing working lists
    # keep zero as their average so deployment never changes concert timing.
    for name, initial in (("groups", "5"), ("setlists", "0")):
        op.add_column(
            name,
            sa.Column(
                "default_pause_seconds", sa.Integer(), nullable=False, server_default=initial
            ),
        )
        op.alter_column(name, "default_pause_seconds", server_default=None)
        op.create_check_constraint(
            op.f(f"ck_{name}_default_pause"), name, "default_pause_seconds BETWEEN 0 AND 86400"
        )
    op.alter_column("items", "pause_after", existing_type=sa.Integer(), nullable=True)
    op.execute("UPDATE items SET pause_after = NULL WHERE pause_after = 0")
    # JSON drafts and immutable revision/submission snapshots remain untouched.


def downgrade():
    # Materialize effective pauses for the previous schema and application.
    op.execute(
        "UPDATE items SET pause_after = setlists.default_pause_seconds "
        "FROM blocks JOIN setlists ON blocks.setlist_id = setlists.id "
        "WHERE items.block_id = blocks.id AND items.pause_after IS NULL"
    )
    connection = op.get_bind()
    for name, column in (
        ("personal_drafts", "content"),
        ("revisions", "snapshot"),
        ("submissions", "snapshot"),
    ):
        document_table = sa.table(name, sa.column("id", sa.Uuid()), sa.column(column, JSONB()))
        for identifier, document in connection.execute(sa.select(document_table)).yield_per(100):
            legacy = deepcopy(document)
            default = legacy.pop("default_pause_seconds", 0)
            for block in legacy["blocks"]:
                for item in block["items"]:
                    if item["pause_after"] is None:
                        item["pause_after"] = default
            if legacy != document:
                connection.execute(
                    document_table.update()
                    .where(document_table.c.id == identifier)
                    .values({column: legacy})
                )
    op.alter_column("items", "pause_after", existing_type=sa.Integer(), nullable=False)
    for name in ("setlists", "groups"):
        op.drop_constraint(op.f(f"ck_{name}_default_pause"), name, type_="check")
        op.drop_column(name, "default_pause_seconds")
