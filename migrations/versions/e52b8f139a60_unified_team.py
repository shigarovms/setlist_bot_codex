"""Unify membership and permanent roles, inviting legacy contacts without granting access."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e52b8f139a60"
down_revision = "c91a72e6b304"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column("memberships", sa.Column("invitation", JSONB(none_as_null=True), nullable=True))
    op.drop_constraint(op.f("ck_memberships_status"), "memberships", type_="check")
    op.create_check_constraint(
        op.f("ck_memberships_status"),
        "memberships",
        "status IN ('pending','active','invited','left')",
    )
    connection = op.get_bind()
    meta = sa.MetaData()
    members = sa.Table("memberships", meta, autoload_with=connection)
    outbox = sa.Table("outbox", meta, autoload_with=connection)
    rows = (
        connection.execute(
            sa.text("""
        SELECT c.group_id,c.user_id,jsonb_agg(DISTINCT r.name) AS roles,
               jsonb_object_agg(c.role_id::text,c.priority) AS priorities,
               g.lifecycle_version, u.telegram_id,
               (SELECT m.user_id FROM memberships m WHERE m.group_id=c.group_id
                AND m.status='active' AND m.role='admin'
                ORDER BY (m.user_id=g.owner_id) DESC, m.id LIMIT 1) AS sender
        FROM staff_contacts c JOIN staff_roles r ON r.id=c.role_id
        JOIN groups g ON g.id=c.group_id JOIN users u ON u.id=c.user_id
        WHERE c.permanent AND NOT EXISTS
          (SELECT 1 FROM memberships m WHERE m.group_id=c.group_id
           AND m.user_id=c.user_id AND m.status='active')
        GROUP BY c.group_id,c.user_id,g.lifecycle_version,g.owner_id,u.telegram_id
    """)
        )
        .mappings()
        .all()
    )
    for row in rows:
        old = (
            connection.execute(
                sa.select(members).where(
                    members.c.group_id == row["group_id"], members.c.user_id == row["user_id"]
                )
            )
            .mappings()
            .first()
        )
        identifier = old["id"] if old else uuid4()
        version = (old["version"] if old else 0) + 1
        invitation = {
            "roles": row["roles"],
            "sender_id": str(row["sender"]) if row["sender"] else "",
            "epoch": row["lifecycle_version"],
            "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "legacy": True,
            "legacy_priorities": row["priorities"],
        }
        if old:
            connection.execute(
                members.update()
                .where(members.c.id == identifier)
                .values(invitation=invitation, version=version)
            )
        else:
            connection.execute(
                members.insert().values(
                    id=identifier,
                    group_id=row["group_id"],
                    user_id=row["user_id"],
                    role="member",
                    musical_role="",
                    status="invited",
                    version=version,
                    invitation=invitation,
                )
            )
        if row["sender"]:
            connection.execute(
                outbox.insert().values(
                    id=uuid4(),
                    group_id=row["group_id"],
                    chat_id=row["telegram_id"],
                    text="Team invitation",
                    sent=False,
                    buttons=[],
                    support_kind="team",
                    created_at=datetime.now(UTC),
                    payload={"member_id": str(identifier), "version": version},
                )
            )
    # Roles remain as contacts, with their priorities and history, until consent.
    connection.execute(
        sa.text("""UPDATE staff_contacts c SET permanent=false WHERE permanent
        AND NOT EXISTS (SELECT 1 FROM memberships m WHERE m.group_id=c.group_id
        AND m.user_id=c.user_id AND m.status='active')""")
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM memberships WHERE invitation IS NOT NULL "
            "OR status IN ('invited','left')"
        )
    ):
        raise RuntimeError(
            "Downgrade would lose team invitations or membership history; restore a backup instead"
        )
    op.execute("DELETE FROM outbox WHERE support_kind IN ('team','team_note')")
    op.drop_constraint(op.f("ck_memberships_status"), "memberships", type_="check")
    op.create_check_constraint(
        op.f("ck_memberships_status"), "memberships", "status IN ('pending','active')"
    )
    op.drop_column("memberships", "invitation")
