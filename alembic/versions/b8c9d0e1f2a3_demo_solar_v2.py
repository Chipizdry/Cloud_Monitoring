"""add demo solar battery v2 tables

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demo_solar_battery_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False, comment="Название объекта"),
        sa.Column(
            "peak_power_kw",
            sa.Float(),
            nullable=False,
            comment="Пиковая мощность объекта, kW",
        ),
        sa.Column(
            "battery_capacity_kwh",
            sa.Float(),
            nullable=False,
            comment="Емкость АКБ объекта, kWh",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "demo_solar_battery_object_algorithms",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("object_id", sa.String(length=36), nullable=False),
        sa.Column(
            "support_peak_minutes",
            sa.Integer(),
            nullable=False,
            comment="Сколько минут АКБ должна держать пиковую мощность объекта",
        ),
        sa.Column(
            "correction_threshold_percent",
            sa.Float(),
            nullable=False,
            server_default="10",
            comment="Допустимое изменение отдачи за период, %",
        ),
        sa.Column(
            "min_soc_percent",
            sa.Float(),
            nullable=False,
            server_default="20",
            comment="Минимальный SOC АКБ, %",
        ),
        sa.Column(
            "recalculation_period_minutes",
            sa.Integer(),
            nullable=False,
            server_default="16",
            comment="Период пересчета алгоритма, минуты",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["object_id"], ["demo_solar_battery_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_id"),
    )
    op.create_index(
        op.f("ix_demo_solar_battery_object_algorithms_object_id"),
        "demo_solar_battery_object_algorithms",
        ["object_id"],
        unique=False,
    )

    op.create_table(
        "demo_solar_battery_global_algorithms",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "external_battery_capacity_kwh",
            sa.Float(),
            nullable=False,
            comment="Емкость внешней АКБ, kWh",
        ),
        sa.Column(
            "support_power_kw",
            sa.Float(),
            nullable=True,
            comment="Заданная мощность поддержки внешней АКБ, kW",
        ),
        sa.Column(
            "support_peak_minutes",
            sa.Integer(),
            nullable=False,
            server_default="16",
            comment="Сколько минут внешняя АКБ должна держать заданную мощность",
        ),
        sa.Column(
            "correction_threshold_percent",
            sa.Float(),
            nullable=False,
            server_default="10",
            comment="Допустимое изменение общей отдачи за период, %",
        ),
        sa.Column(
            "min_soc_percent",
            sa.Float(),
            nullable=False,
            server_default="20",
            comment="Минимальный SOC внешней АКБ, %",
        ),
        sa.Column(
            "recalculation_period_minutes",
            sa.Integer(),
            nullable=False,
            server_default="16",
            comment="Период пересчета глобального алгоритма, минуты",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("demo_solar_battery_global_algorithms")
    op.drop_index(
        op.f("ix_demo_solar_battery_object_algorithms_object_id"),
        table_name="demo_solar_battery_object_algorithms",
    )
    op.drop_table("demo_solar_battery_object_algorithms")
    op.drop_table("demo_solar_battery_objects")
