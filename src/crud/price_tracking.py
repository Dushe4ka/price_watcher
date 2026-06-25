from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.product_price_history import ProductPriceHistory
from src.models.tracked_product import TrackedProduct
from src.parsers.base import ParsedProduct


class TrackedProductCRUD:
    async def get_or_create(
        self,
        session: AsyncSession,
        product: ParsedProduct,
        marketplace: str,
        category_slug: str,
    ) -> TrackedProduct:
        result = await session.execute(
            select(TrackedProduct).where(
                TrackedProduct.marketplace == marketplace,
                TrackedProduct.external_id == product.external_id,
            )
        )
        tracked = result.scalar_one_or_none()
        if tracked is None:
            tracked = TrackedProduct(
                marketplace=marketplace,
                external_id=product.external_id,
                title=product.title,
                category_slug=category_slug,
                product_url=product.product_url,
                image_url=product.image_url,
                last_price=product.price,
            )
            session.add(tracked)
        else:
            tracked.title = product.title
            tracked.category_slug = category_slug
            tracked.product_url = product.product_url or tracked.product_url
            tracked.image_url = product.image_url or tracked.image_url
            tracked.last_price = product.price
            session.add(tracked)
        await session.commit()
        await session.refresh(tracked)
        return tracked

    async def get_by_marketplace_and_external_id(
        self,
        session: AsyncSession,
        marketplace: str,
        external_id: str,
    ) -> TrackedProduct | None:
        result = await session.execute(
            select(TrackedProduct).where(
                TrackedProduct.marketplace == marketplace,
                TrackedProduct.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()


tracked_product_crud = TrackedProductCRUD()


class ProductPriceHistoryCRUD:
    async def add_record(
        self,
        session: AsyncSession,
        tracked_product_id: int,
        price: Decimal,
        parser_original_price: Decimal | None,
        parser_discount_percent: int | None,
    ) -> ProductPriceHistory:
        record = ProductPriceHistory(
            tracked_product_id=tracked_product_id,
            price=price,
            parser_original_price=parser_original_price,
            parser_discount_percent=parser_discount_percent,
            recorded_at=datetime.now(timezone.utc),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record

    async def get_average_price(
        self,
        session: AsyncSession,
        tracked_product_id: int,
        days: int | None = None,
    ) -> Decimal | None:
        retention_days = days or settings.price_history_retention_days
        since = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await session.execute(
            select(func.avg(ProductPriceHistory.price)).where(
                ProductPriceHistory.tracked_product_id == tracked_product_id,
                ProductPriceHistory.recorded_at >= since,
            )
        )
        avg_value = result.scalar()
        if avg_value is None:
            return None
        return Decimal(str(avg_value)).quantize(Decimal('0.01'))

    async def get_oldest_record_time(
        self,
        session: AsyncSession,
    ) -> datetime | None:
        result = await session.execute(
            select(func.min(ProductPriceHistory.recorded_at))
        )
        return result.scalar()

    async def delete_older_than(
        self,
        session: AsyncSession,
        days: int | None = None,
    ) -> int:
        retention_days = days or settings.price_history_retention_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await session.execute(
            delete(ProductPriceHistory).where(
                ProductPriceHistory.recorded_at < cutoff
            )
        )
        await session.commit()
        return result.rowcount or 0


product_price_history_crud = ProductPriceHistoryCRUD()
