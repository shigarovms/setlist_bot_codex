"""Collective timing coordinator and proposal delivery date.

Revision ID: f674ef62bc3f
Revises: d2e64c3309a1
"""

import sqlalchemy as sa
from alembic import op

revision = "f674ef62bc3f"
down_revision = "d2e64c3309a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("groups", sa.Column("timing_coordinator_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_groups_timing_coordinator_id_users",
        "groups",
        "users",
        ["timing_coordinator_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Use the nearest upcoming assignment when a collective had several coordinators.
    op.execute("""
        UPDATE groups AS g SET timing_coordinator_id = assignment.user_id
        FROM (
            SELECT DISTINCT ON (c.group_id) c.group_id, m.user_id
            FROM concerts AS c
            JOIN memberships AS m ON m.group_id = c.group_id
                AND m.user_id::text = c.timing->>'responsible' AND m.status = 'active'
            WHERE c.deleted_at IS NULL AND c.date >= (now() AT TIME ZONE 'Europe/Moscow')::date
            ORDER BY c.group_id, c.date, c.id
        ) AS assignment WHERE assignment.group_id = g.id
    """)
    op.add_column("submissions", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column(
        "submissions",
        sa.Column("pending_delivery", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("""
        UPDATE submissions AS s
        SET published_at = COALESCE(o.delivered_at, o.created_at),
            pending_delivery = (o.delivered_at IS NULL AND NOT s.applied AND NOT s.declined
                AND (NOT o.sent OR o.last_error <> '' OR o.discarded_at IS NOT NULL))
        FROM outbox AS o
        WHERE s.group_id = o.group_id AND s.concert_id = o.concert_id
            AND o.buttons->0->>1 IN (
                'apply_submission ' || s.id::text, 'view_submission ' || s.id::text
            )
    """)


def downgrade() -> None:
    op.drop_column("submissions", "pending_delivery")
    op.drop_column("submissions", "published_at")
    op.drop_constraint("fk_groups_timing_coordinator_id_users", "groups", type_="foreignkey")
    op.drop_column("groups", "timing_coordinator_id")
