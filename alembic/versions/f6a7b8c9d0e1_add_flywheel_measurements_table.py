"""add flywheel measurements table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flywheel_measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("energetic_object_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("measured_at", sa.DateTime(), nullable=False, comment="Дата и время измерения"),
        sa.Column("flywheel_speed_rpm", sa.Float(), nullable=True, comment="Скорость маховика в оборотах в минуту"),
        sa.Column("flywheel_pwm", sa.Float(), nullable=True, comment="Текущее значение ШИМ для управления маховиком"),
        sa.Column("flywheel_frequency", sa.Float(), nullable=True, comment="Текущая частота управления маховиком в Гц"),
        sa.Column("flywheel_arr_timer", sa.Float(), nullable=True, comment="Текущее значение ARR таймера для управления маховиком"),
        sa.Column("flywheel_pwm_raw", sa.Float(), nullable=True, comment="Текущее сырое значение ШИМ"),
        sa.Column("flywheel_voltage", sa.Float(), nullable=True, comment="Напряжение"),
        sa.Column("flywheel_current", sa.Float(), nullable=True, comment="Ток"),
        sa.Column("flywheel_power", sa.Float(), nullable=True, comment="Мощность"),
        sa.Column(
            "stator_temperature",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Значения температур статоров в виде массива JSON",
        ),
        sa.ForeignKeyConstraint(["energetic_object_id"], ["energetic_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_flywheel_measurements_energetic_object_id",
        "flywheel_measurements",
        ["energetic_object_id"],
        unique=False,
    )
    op.create_index(
        "ix_flywheel_measurements_measured_at",
        "flywheel_measurements",
        ["measured_at"],
        unique=False,
    )
    op.create_index(
        "ix_flywheel_measurements_object_measured_at",
        "flywheel_measurements",
        ["energetic_object_id", "measured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_flywheel_measurements_object_measured_at", table_name="flywheel_measurements")
    op.drop_index("ix_flywheel_measurements_measured_at", table_name="flywheel_measurements")
    op.drop_index("ix_flywheel_measurements_energetic_object_id", table_name="flywheel_measurements")
    op.drop_table("flywheel_measurements")
