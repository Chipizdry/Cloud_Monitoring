"""add power measurements table

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-03 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "power_measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("energetic_object_id", sa.String(length=36), nullable=False),
        sa.Column("object_name", sa.String(), nullable=True),
        sa.Column("source_protocol", sa.String(), nullable=True),
        sa.Column("source_task_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("solar_power_w", sa.Float(), nullable=True),
        sa.Column("battery_power_w", sa.Float(), nullable=True),
        sa.Column("battery_soc", sa.Float(), nullable=True),
        sa.Column("grid_power_w", sa.Float(), nullable=True),
        sa.Column("load_power_w", sa.Float(), nullable=True),
        sa.Column("generator_power_w", sa.Float(), nullable=True),
        sa.Column("raw_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["energetic_object_id"], ["energetic_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_power_measurements_energetic_object_id"),
        "power_measurements",
        ["energetic_object_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_power_measurements_measured_at"),
        "power_measurements",
        ["measured_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_power_measurements_object_name"),
        "power_measurements",
        ["object_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_power_measurements_source_protocol"),
        "power_measurements",
        ["source_protocol"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_power_measurements_source_protocol"), table_name="power_measurements")
    op.drop_index(op.f("ix_power_measurements_object_name"), table_name="power_measurements")
    op.drop_index(op.f("ix_power_measurements_measured_at"), table_name="power_measurements")
    op.drop_index(op.f("ix_power_measurements_energetic_object_id"), table_name="power_measurements")
    op.drop_table("power_measurements")
