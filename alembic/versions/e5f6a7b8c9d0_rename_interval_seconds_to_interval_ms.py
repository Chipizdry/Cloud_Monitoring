"""rename interval_seconds to interval_ms

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-27

"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename column and multiply existing values by 1000 (seconds → milliseconds)
    op.alter_column(
        "device_polling_tasks",
        "interval_seconds",
        new_column_name="interval_ms",
        existing_type=sa.Float(),
        existing_nullable=False,
        comment="Интервал опроса в миллисекундах",
    )
    op.execute("UPDATE device_polling_tasks SET interval_ms = interval_ms * 1000")

    op.alter_column(
        "websocket_broadcast_tasks",
        "interval_seconds",
        new_column_name="interval_ms",
        existing_type=sa.Float(),
        existing_nullable=False,
        comment="Интервал отправки команды в миллисекундах",
    )
    op.execute("UPDATE websocket_broadcast_tasks SET interval_ms = interval_ms * 1000")


def downgrade() -> None:
    # Convert back: divide by 1000, rename to interval_seconds
    op.execute("UPDATE device_polling_tasks SET interval_ms = interval_ms / 1000")
    op.alter_column(
        "device_polling_tasks",
        "interval_ms",
        new_column_name="interval_seconds",
        existing_type=sa.Float(),
        existing_nullable=False,
        comment="Интервал опроса в секундах",
    )

    op.execute("UPDATE websocket_broadcast_tasks SET interval_ms = interval_ms / 1000")
    op.alter_column(
        "websocket_broadcast_tasks",
        "interval_ms",
        new_column_name="interval_seconds",
        existing_type=sa.Float(),
        existing_nullable=False,
        comment="Интервал отправки команды в секундах",
    )
