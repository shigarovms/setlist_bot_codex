"""Record declined setlist proposals."""

import sqlalchemy as sa
from alembic import op

revision = "cdd481792e03"
down_revision = "e52b8f139a60"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("declined", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_submissions_single_decision"), "submissions", "NOT (applied AND declined)"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_submissions_single_decision"), "submissions", type_="check")
    op.drop_column("submissions", "declined")
