"""Record lineup decisions and nominated replacement candidates."""

import sqlalchemy as sa
from alembic import op

revision = "d2e64c3309a1"
down_revision = "5ac5b58ec9d1"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column(
        "concerts",
        sa.Column("lineup_optional", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "staff_replacements",
        sa.Column("candidate_ref", sa.String(32), server_default="", nullable=False),
    )
    op.add_column(
        "staff_replacements",
        sa.Column("candidate_link_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )

    op.add_column("staff_replacements", sa.Column("candidate_link_token", sa.Uuid()))
    op.create_unique_constraint(
        op.f("uq_staff_replacements_candidate_link_token"),
        "staff_replacements",
        ["candidate_link_token"],
    )


def downgrade() -> None:
    op.drop_column("staff_replacements", "candidate_link_token")
    op.drop_column("staff_replacements", "candidate_link_by")
    op.drop_column("staff_replacements", "candidate_ref")
    op.drop_column("concerts", "lineup_optional")
