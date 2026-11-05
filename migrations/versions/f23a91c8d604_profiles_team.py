"""Profiles and permanent performers, without rewriting existing bookings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f23a91c8d604"
down_revision = "d71f439e8a20"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("profile", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "users", sa.Column("profile_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "lineup_templates",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "staff_contacts",
        sa.Column("permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "staff_contacts", sa.Column("priority", sa.Integer(), nullable=False, server_default="100")
    )
    op.add_column(
        "staff_contacts", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_check_constraint("profile_version", "users", "profile_version >= 0")
    op.create_check_constraint(
        "contact_version", "staff_contacts", "priority BETWEEN 0 AND 9999 AND version > 0"
    )
    # RU: Используем журнал для прежних дат; неизвестную хронологию не выдумываем.
    # EN: Recover creation timestamps from audit; otherwise use a stable migration-time tie.
    op.execute("""UPDATE lineup_templates t SET created_at=a.first_at FROM
        (SELECT target, min(created_at) AS first_at FROM admin_audit
         WHERE action='lineup_template' GROUP BY target) a WHERE a.target=t.id::text""")
    # RU/EN: Existing saved defaults become group-level performers; original JSON stays intact.
    op.execute("""UPDATE staff_contacts c SET permanent=true WHERE EXISTS (
        SELECT 1 FROM lineup_templates t, jsonb_array_elements(t.slots) s
        WHERE t.group_id=c.group_id AND s->>'role_id'=c.role_id::text
        AND s->>'user_id'=c.user_id::text)""")


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM users WHERE profile_version>0) "
            "OR EXISTS(SELECT 1 FROM staff_contacts WHERE permanent OR priority<>100)"
        )
    ):
        raise RuntimeError("Downgrade would lose profile/team data; preserve a backup.")
    op.drop_constraint(op.f("ck_staff_contacts_contact_version"), "staff_contacts", type_="check")
    op.drop_constraint(op.f("ck_users_profile_version"), "users", type_="check")
    for name in ("version", "priority", "permanent"):
        op.drop_column("staff_contacts", name)
    op.drop_column("lineup_templates", "created_at")
    op.drop_column("users", "profile_version")
    op.drop_column("users", "profile")
