"""Concert details, personal PDF settings, catalog creation time and rich outbox text."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d31a7b920e64"
down_revision = "a6f8d31b2e90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "concerts", sa.Column("performance_duration_seconds", sa.Integer(), nullable=True)
    )
    op.add_column("concerts", sa.Column("city", sa.String(200), nullable=False, server_default=""))
    op.add_column("concerts", sa.Column("extra_info", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "concerts", sa.Column("metadata_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint(
        op.f("ck_concerts_performance_duration"),
        "concerts",
        "performance_duration_seconds > 0 AND performance_duration_seconds <= 86400",
    )
    op.add_column(
        "users",
        sa.Column(
            "export_settings", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column(
        "songs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "outbox", sa.Column("monospace_offset", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM concerts WHERE "
                "performance_duration_seconds IS NOT NULL "
                "OR city <> '' OR extra_info <> '') OR EXISTS (SELECT 1 FROM users "
                "WHERE export_settings <> '{}'::jsonb) OR EXISTS (SELECT 1 FROM outbox "
                "WHERE monospace_offset <> 0 AND NOT sent)"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError(
            "Concert details, export settings or rich messages exist; downgrade would lose data"
        )
    op.drop_column("outbox", "monospace_offset")
    op.drop_column("songs", "created_at")
    op.drop_column("users", "export_settings")
    op.drop_constraint(op.f("ck_concerts_performance_duration"), "concerts", type_="check")
    for name in ("metadata_version", "extra_info", "city", "performance_duration_seconds"):
        op.drop_column("concerts", name)
