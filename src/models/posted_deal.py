from decimal import Decimal

from sqlalchemy import Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database.annotations import int_pk, not_null_str
from src.models.base import Base


class PostedDeal(Base):
    """Опубликованная скидка в Telegram-канале."""

    id: Mapped[int_pk]
    marketplace: Mapped[not_null_str]
    external_id: Mapped[not_null_str]
    category_slug: Mapped[not_null_str]
    hashtag: Mapped[not_null_str]
    title: Mapped[not_null_str]
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    original_price: Mapped[Decimal | None] = mapped_column(
        Numeric, nullable=True
    )
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            'marketplace',
            'external_id',
            name='unique_marketplace_external_id',
        ),
    )
