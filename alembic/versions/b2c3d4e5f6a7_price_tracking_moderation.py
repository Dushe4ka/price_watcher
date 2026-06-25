"""price tracking and deal moderation tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

moderation_status = postgresql.ENUM(
    'pending',
    'approved',
    'rejected',
    'auto_posted',
    'skipped',
    name='moderationstatus',
    create_type=False,
)


def upgrade() -> None:
    moderation_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'trackedproduct',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('marketplace', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('category_slug', sa.String(), nullable=False),
        sa.Column('product_url', sa.String(length=2048), nullable=True),
        sa.Column('image_url', sa.String(length=2048), nullable=True),
        sa.Column('last_price', sa.Numeric(), nullable=True),
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
            name='unique_tracked_product_marketplace_external_id',
        ),
    )

    op.create_table(
        'productpricehistory',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tracked_product_id', sa.Integer(), nullable=False),
        sa.Column('price', sa.Numeric(), nullable=False),
        sa.Column('parser_original_price', sa.Numeric(), nullable=True),
        sa.Column('parser_discount_percent', sa.Integer(), nullable=True),
        sa.Column(
            'recorded_at',
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ['tracked_product_id'],
            ['trackedproduct.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )
    op.create_index(
        'ix_productpricehistory_tracked_product_id',
        'productpricehistory',
        ['tracked_product_id'],
    )
    op.create_index(
        'ix_productpricehistory_recorded_at',
        'productpricehistory',
        ['recorded_at'],
    )

    op.create_table(
        'dealmoderation',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tracked_product_id', sa.Integer(), nullable=True),
        sa.Column('marketplace', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('category_slug', sa.String(), nullable=False),
        sa.Column('hashtag', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('price', sa.Numeric(), nullable=False),
        sa.Column('original_price', sa.Numeric(), nullable=True),
        sa.Column('average_price', sa.Numeric(), nullable=True),
        sa.Column('parser_discount_percent', sa.Integer(), nullable=True),
        sa.Column('database_discount_percent', sa.Integer(), nullable=True),
        sa.Column('product_url', sa.String(length=2048), nullable=True),
        sa.Column('image_url', sa.String(length=2048), nullable=True),
        sa.Column(
            'status',
            moderation_status,
            nullable=False,
        ),
        sa.Column('decision_reason', sa.String(), nullable=False),
        sa.Column('admin_telegram_id', sa.Integer(), nullable=True),
        sa.Column('admin_message_id', sa.Integer(), nullable=True),
        sa.Column('channel_message_id', sa.Integer(), nullable=True),
        sa.Column(
            'resolved_at',
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(
            ['tracked_product_id'],
            ['trackedproduct.id'],
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('dealmoderation')
    op.drop_index(
        'ix_productpricehistory_recorded_at',
        table_name='productpricehistory',
    )
    op.drop_index(
        'ix_productpricehistory_tracked_product_id',
        table_name='productpricehistory',
    )
    op.drop_table('productpricehistory')
    op.drop_table('trackedproduct')
    moderation_status.drop(op.get_bind(), checkfirst=True)
