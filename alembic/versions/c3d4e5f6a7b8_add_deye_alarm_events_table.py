"""add deye alarm events table

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-03-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deye_alarm_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('energetic_object_id', sa.String(length=36), nullable=False),
        sa.Column('object_name', sa.String(), nullable=True),
        sa.Column('measured_at', sa.DateTime(), nullable=False, comment='Время snapshot состояния алармов'),
        sa.Column('source_task_id', sa.String(), nullable=True, comment='ID polling task, которая зафиксировала событие'),
        sa.Column('fault_word_1', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fault_word_2', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('warning_word_1', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('warning_word_2', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('faults', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['energetic_object_id'], ['energetic_objects.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_deye_alarm_events_energetic_object_id', 'deye_alarm_events', ['energetic_object_id'], unique=False)
    op.create_index('ix_deye_alarm_events_object_name', 'deye_alarm_events', ['object_name'], unique=False)
    op.create_index('ix_deye_alarm_events_measured_at', 'deye_alarm_events', ['measured_at'], unique=False)
    op.create_index('ix_deye_alarm_events_object_measured_at', 'deye_alarm_events', ['energetic_object_id', 'measured_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_deye_alarm_events_object_measured_at', table_name='deye_alarm_events')
    op.drop_index('ix_deye_alarm_events_measured_at', table_name='deye_alarm_events')
    op.drop_index('ix_deye_alarm_events_object_name', table_name='deye_alarm_events')
    op.drop_index('ix_deye_alarm_events_energetic_object_id', table_name='deye_alarm_events')
    op.drop_table('deye_alarm_events')
