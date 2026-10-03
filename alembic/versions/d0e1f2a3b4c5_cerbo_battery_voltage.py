"""add battery voltage to cerbo measurements

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-16 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cerbo_measurements",
        sa.Column("battery_voltage", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cerbo_measurements", "battery_voltage")
