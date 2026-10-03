"""update timezone Kiev to Kyiv

Revision ID: a1b2c3d4e5f6
Revises: 11c935d61c02
Create Date: 2026-01-28 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '11c935d61c02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Обновляем дефолтное значение колонки timezone
    op.alter_column(
        'energetic_objects',
        'timezone',
        server_default='Europe/Kyiv',
        existing_type=sa.String(),
        existing_nullable=True,
        existing_comment='Часовой пояс объекта (например: Europe/Kyiv)'
    )
    
    # Обновляем все существующие записи с Europe/Kiev на Europe/Kyiv
    op.execute(
        """
        UPDATE energetic_objects 
        SET timezone = 'Europe/Kyiv' 
        WHERE timezone = 'Europe/Kiev'
        """
    )


def downgrade() -> None:
    # Откатываем обратно на Europe/Kiev
    op.alter_column(
        'energetic_objects',
        'timezone',
        server_default='Europe/Kiev',
        existing_type=sa.String(),
        existing_nullable=True,
        existing_comment='Часовой пояс объекта (например: Europe/Kiev)'
    )
    
    op.execute(
        """
        UPDATE energetic_objects 
        SET timezone = 'Europe/Kiev' 
        WHERE timezone = 'Europe/Kyiv'
        """
    )
