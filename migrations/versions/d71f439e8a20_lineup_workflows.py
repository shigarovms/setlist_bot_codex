"""Saved lineups, soft role removal and durable invitation delivery metadata."""

import sqlalchemy as sa
from alembic import op

revision = "d71f439e8a20"
down_revision = "b63e2a71d940"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "concerts", sa.Column("lineup_name", sa.String(100), nullable=False, server_default="")
    )
    op.add_column(
        "concerts", sa.Column("lineup_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "gig_slots", sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "gig_invitations", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "gig_invitations", sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True)
    )
    # RU: Участник, уже ответивший на приглашение, точно о нём знает.
    # EN: Preserve delivery knowledge on upgrade; an answered invite was seen.
    op.execute(
        "UPDATE gig_invitations SET notified_at=created_at WHERE status IN "
        "('confirmed','conditional','accepted','declined','replaced')"
    )
    op.execute("""UPDATE gig_invitations i SET notified_at=o.delivered_at
      FROM (SELECT payload->>'invite_id' AS id, min(delivered_at) AS delivered_at
        FROM outbox WHERE support_kind='staffing' AND delivered_at IS NOT NULL
        AND coalesce(payload->>'admin','false') != 'true' GROUP BY payload->>'invite_id') o
      WHERE i.id::text=o.id AND i.notified_at IS NULL""")


def downgrade():
    # RU/EN: Never silently erase a removed role or the selected lineup name.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM gig_slots WHERE removed) "
            "OR EXISTS(SELECT 1 FROM concerts WHERE lineup_name <> '')"
        )
    ):
        raise RuntimeError("Downgrade would lose lineup data; preserve a backup.")
    op.drop_column("gig_invitations", "last_reminded_at")
    op.drop_column("gig_invitations", "notified_at")
    op.drop_column("gig_slots", "removed")
    op.drop_column("concerts", "lineup_version")
    op.drop_column("concerts", "lineup_name")
