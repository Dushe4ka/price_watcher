from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.annotations import int_pk, optional_utc_datetime, utc_datetime
from src.models.base import Base


class ProductPriceHistory(Base):
    """История цен товара (хранится PRICE_HISTORY_RETENTION_DAYS дней)."""

    id: Mapped[int_pk]
    tracked_product_id: Mapped[int] = mapped_column(
        ForeignKey('trackedproduct.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    parser_original_price: Mapped[Decimal | None] = mapped_column(
        Numeric, nullable=True
    )
    parser_discount_percent: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    recorded_at: Mapped[utc_datetime]

    product: Mapped['TrackedProduct'] = relationship(
        'TrackedProduct',
        back_populates='price_history',
        lazy='selectin',
    )
