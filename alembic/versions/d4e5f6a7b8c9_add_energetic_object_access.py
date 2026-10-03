"""add energetic object access

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    access_level_enum = postgresql.ENUM(
        "READ",
        "READ_WRITE",
        "SHARE",
        name="accesslevel",
        create_type=False,
    )

    op.add_column("energetic_objects", sa.Column("owner_cor_id", sa.String(length=250), nullable=True))
    op.create_index(op.f("ix_energetic_objects_owner_cor_id"), "energetic_objects", ["owner_cor_id"], unique=False)
    op.create_foreign_key(
        "fk_energetic_objects_owner_cor_id_users",
        "energetic_objects",
        "users",
        ["owner_cor_id"],
        ["cor_id"],
    )

    op.create_table(
        "energetic_object_access",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("energetic_object_id", sa.String(length=36), nullable=False),
        sa.Column("granting_user_cor_id", sa.String(length=36), nullable=True),
        sa.Column("accessing_user_cor_id", sa.String(length=36), nullable=False),
        sa.Column("access_level", access_level_enum, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["accessing_user_cor_id"], ["users.cor_id"]),
        sa.ForeignKeyConstraint(["energetic_object_id"], ["energetic_objects.id"]),
        sa.ForeignKeyConstraint(["granting_user_cor_id"], ["users.cor_id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("energetic_object_access")
    op.drop_constraint("fk_energetic_objects_owner_cor_id_users", "energetic_objects", type_="foreignkey")
    op.drop_index(op.f("ix_energetic_objects_owner_cor_id"), table_name="energetic_objects")
    op.drop_column("energetic_objects", "owner_cor_id")
