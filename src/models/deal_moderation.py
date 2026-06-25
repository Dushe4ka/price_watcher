from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.annotations import int_pk, not_null_str, optional_utc_datetime
from src.database.enums import ModerationStatus
from src.models.base import Base


class DealModeration(Base):
    """Решение по публикации скидки (авто, пропуск, модерация админом)."""

    id: Mapped[int_pk]
    tracked_product_id: Mapped[int | None] = mapped_column(
        ForeignKey('trackedproduct.id', ondelete='SET NULL'),
        nullable=True,
    )
    marketplace: Mapped[not_null_str]
    external_id: Mapped[not_null_str]
    category_slug: Mapped[not_null_str]
    hashtag: Mapped[not_null_str]
    title: Mapped[not_null_str]
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    original_price: Mapped[Decimal | None] = mapped_column(
        Numeric, nullable=True
    )
    average_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    parser_discount_percent: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    database_discount_percent: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    product_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[ModerationStatus] = mapped_column(nullable=False)
    decision_reason: Mapped[not_null_str]
    admin_telegram_id: Mapped[int | None] = mapped_column(nullable=True)
    admin_message_id: Mapped[int | None] = mapped_column(nullable=True)
    channel_message_id: Mapped[int | None] = mapped_column(nullable=True)
    resolved_at: Mapped[optional_utc_datetime]
