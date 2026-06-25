from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.annotations import int_pk, not_null_str
from src.models.base import Base

UNIQUE_TRACKED_PRODUCT_CONSTRAINT = 'unique_tracked_product_marketplace_external_id'


class TrackedProduct(Base):
    """Карточка товара, собираемая при обходе категорий."""

    id: Mapped[int_pk]
    marketplace: Mapped[not_null_str]
    external_id: Mapped[not_null_str]
    title: Mapped[not_null_str]
    category_slug: Mapped[not_null_str]
    product_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)

    price_history: Mapped[list['ProductPriceHistory']] = relationship(
        'ProductPriceHistory',
        back_populates='product',
        lazy='selectin',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        UniqueConstraint(
            'marketplace',
            'external_id',
            name=UNIQUE_TRACKED_PRODUCT_CONSTRAINT,
        ),
    )
