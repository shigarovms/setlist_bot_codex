"""Group ownership and daily concert reminder deduplication."""

import sqlalchemy as sa
from alembic import op

revision = "f8129a4b6c30"
down_revision = "e42b8c190a76"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("groups", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_groups_owner_id_users"), "groups", "users", ["owner_id"], ["id"])
    # Existing releases allowed exactly one administrator, who created the group.
    ambiguous = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT group_id FROM memberships WHERE role='admin' AND status='active' "
                "GROUP BY group_id HAVING count(*) > 1"
            )
        )
        .first()
    )
    if ambiguous:
        raise RuntimeError("Cannot infer owner: multiple legacy administrators")
    op.execute(
        "UPDATE groups g SET owner_id=m.user_id FROM memberships m "
        "WHERE m.group_id=g.id AND m.role='admin' AND m.status='active'"
    )
    op.add_column(
        "groups",
        sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("concert_id", sa.Uuid(), sa.ForeignKey("concerts.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("outbox_id", sa.Uuid(), sa.ForeignKey("outbox.id"), nullable=False),
        sa.UniqueConstraint("outbox_id"),
        sa.UniqueConstraint("concert_id", "day"),
    )
    op.create_index("ix_reminders_group_id", "reminders", ["group_id"])
    op.execute(
        "UPDATE revisions r SET snapshot=jsonb_set(r.snapshot, '{approved_by}', "
        "to_jsonb(coalesce(nullif(m.musical_role,''), nullif(u.display_name,''), "
        "u.telegram_id::text))) "
        "FROM users u LEFT JOIN memberships m ON m.user_id=u.id "
        "WHERE r.author_id=u.id AND m.group_id=r.group_id AND r.action='approved' "
        "AND NOT (r.snapshot ? 'approved_by')"
    )

    # Old requests inherited IDs from a different chat. Never use these for deletion.
    op.execute(
        "UPDATE confirmations SET command=(command::jsonb - 'message_ids')::text "
        "WHERE command::jsonb->>'kind'='setlist_request'"
    )
    op.execute(
        "UPDATE export_snapshots e SET payload=jsonb_set(e.payload, "
        "'{snapshot,approved_by}', r.snapshot->'approved_by') FROM revisions r, setlists s "
        "WHERE s.concert_id=e.concert_id AND s.group_id=e.group_id AND r.setlist_id=s.id "
        "AND r.action='approved' AND r.number=(e.payload->>'version')::int "
        "AND e.payload->>'approved'='true' AND r.snapshot ? 'approved_by' "
        "AND NOT (e.payload->'snapshot' ? 'approved_by')"
    )


def downgrade():
    unsafe = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM reminders) OR EXISTS(SELECT 1 FROM groups "
                "WHERE NOT reminders_enabled) OR EXISTS(SELECT 1 FROM memberships "
                "WHERE role='admin' GROUP BY group_id HAVING count(*)>1)"
            )
        )
        .scalar_one()
    )
    if unsafe:
        raise RuntimeError("Downgrade would lose data: ownership or reminders")
    op.drop_table("reminders")
    op.drop_column("groups", "reminders_enabled")
    op.drop_constraint(op.f("fk_groups_owner_id_users"), "groups", type_="foreignkey")
    op.drop_column("groups", "owner_id")
