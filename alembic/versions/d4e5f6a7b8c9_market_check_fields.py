"""add market check fields to deal moderation

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-19 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dealmoderation',
        sa.Column('market_min_price', sa.Numeric(), nullable=True),
    )
    op.add_column(
        'dealmoderation',
        sa.Column('market_discount_percent', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dealmoderation', 'market_discount_percent')
    op.drop_column('dealmoderation', 'market_min_price')
