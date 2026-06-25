"""add posted_deals and yandex_market enum

Revision ID: a1b2c3d4e5f6
Revises: 73dfd3164c51
Create Date: 2026-06-18 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '73dfd3164c51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE marketplace ADD VALUE IF NOT EXISTS 'YANDEX_MARKET'"
    )
    op.create_table(
        'posteddeal',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('marketplace', sa.String(), nullable=True),
        sa.Column('external_id', sa.String(), nullable=True),
        sa.Column('category_slug', sa.String(), nullable=True),
        sa.Column('hashtag', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('price', sa.Numeric(), nullable=False),
        sa.Column('original_price', sa.Numeric(), nullable=True),
        sa.Column('discount_percent', sa.Integer(), nullable=True),
        sa.Column('product_url', sa.String(length=2048), nullable=True),
        sa.Column('image_url', sa.String(length=2048), nullable=True),
        sa.Column('telegram_message_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
        sa.UniqueConstraint(
            'marketplace',
            'external_id',
            name='unique_marketplace_external_id',
        ),
    )


def downgrade() -> None:
    op.drop_table('posteddeal')
