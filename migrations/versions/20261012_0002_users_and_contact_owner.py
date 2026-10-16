"""Add users and an optional owner to existing contacts.

Revision ID: 20261012_0002
Revises: 20261012_0001
"""

from alembic import op
import sqlalchemy as sa


revision = "20261012_0002"
down_revision = "20261012_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_contacts_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
        )
    op.create_index("ix_contacts_owner_id", "contacts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_contacts_owner_id", table_name="contacts")
    with op.batch_alter_table("contacts") as batch_op:
        batch_op.drop_constraint("fk_contacts_owner_id_users", type_="foreignkey")
        batch_op.drop_column("owner_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
