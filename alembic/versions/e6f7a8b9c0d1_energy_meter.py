"""add energy meter measurements

Revision ID: e6f7a8b9c0d1
Revises: d0e1f2a3b4c5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "energy_meter_measurements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("energetic_object_id", sa.String(length=36), sa.ForeignKey("energetic_objects.id"), nullable=False),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("active_power_kw", sa.Float(), nullable=False),
        sa.Column("reactive_power_kvar", sa.Float(), nullable=False),
        sa.Column("apparent_power_kva", sa.Float(), nullable=False),
        sa.Column("power_factor", sa.Float(), nullable=False),
        sa.Column("frequency_hz", sa.Float(), nullable=False),
        sa.Column("angle_deg", sa.Float(), nullable=False),
        sa.Column("voltage_l1_n_v", sa.Float(), nullable=False),
        sa.Column("voltage_l2_n_v", sa.Float(), nullable=False),
        sa.Column("voltage_l3_n_v", sa.Float(), nullable=False),
        sa.Column("voltage_l1_l2_v", sa.Float(), nullable=False),
        sa.Column("voltage_l2_l3_v", sa.Float(), nullable=False),
        sa.Column("voltage_l3_l1_v", sa.Float(), nullable=False),
        sa.Column("apparent_energy_kvah", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_energy_meter_object_time",
        "energy_meter_measurements",
        ["energetic_object_id", "measured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_energy_meter_object_time", table_name="energy_meter_measurements")
    op.drop_table("energy_meter_measurements")
