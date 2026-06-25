"""ensure timestamptz for price history and moderation

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-19 01:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'productpricehistory',
        'recorded_at',
        existing_type=postgresql.TIMESTAMP(),
        type_=postgresql.TIMESTAMP(timezone=True),
        postgresql_using='recorded_at AT TIME ZONE \'UTC\'',
    )
    op.alter_column(
        'dealmoderation',
        'resolved_at',
        existing_type=postgresql.TIMESTAMP(),
        type_=postgresql.TIMESTAMP(timezone=True),
        postgresql_using=(
            'CASE WHEN resolved_at IS NULL THEN NULL '
            'ELSE resolved_at AT TIME ZONE \'UTC\' END'
        ),
    )


def downgrade() -> None:
    op.alter_column(
        'dealmoderation',
        'resolved_at',
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=postgresql.TIMESTAMP(),
        postgresql_using='resolved_at AT TIME ZONE \'UTC\'',
    )
    op.alter_column(
        'productpricehistory',
        'recorded_at',
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=postgresql.TIMESTAMP(),
        postgresql_using='recorded_at AT TIME ZONE \'UTC\'',
    )
