from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.crud.price_tracking import product_price_history_crud
from src.parsers.base import ParsedProduct


class DealAction(StrEnum):
    SKIP = 'skip'
    POST = 'post'
    POST_AVERAGE_NOTE = 'post_average_note'
    MODERATE = 'moderate'


@dataclass(frozen=True, slots=True)
class DiscountDecision:
    action: DealAction
    reason: str
    parser_discount: int | None
    database_discount: int | None
    average_price: Decimal | None


class DiscountEvaluator:
    @staticmethod
    def calc_parser_discount(product: ParsedProduct) -> int | None:
        if product.discount_percent is not None:
            return product.discount_percent
        if (
            product.original_price
            and product.original_price > 0
            and product.price < product.original_price
        ):
            return int(
                (product.original_price - product.price)
                / product.original_price
                * 100
            )
        return None

    @staticmethod
    def calc_database_discount(
        current_price: Decimal,
        average_price: Decimal | None,
    ) -> int | None:
        if average_price is None or average_price <= 0:
            return None
        if current_price >= average_price:
            return None
        return int((average_price - current_price) / average_price * 100)

    async def is_warmup_period(self, session: AsyncSession) -> bool:
        oldest = await product_price_history_crud.get_oldest_record_time(session)
        if oldest is None:
            return True
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - oldest
        return elapsed < timedelta(days=settings.data_collection_warmup_days)

    async def evaluate(
        self,
        session: AsyncSession,
        product: ParsedProduct,
        average_price: Decimal | None,
    ) -> DiscountDecision:
        parser_discount = self.calc_parser_discount(product)
        database_discount = self.calc_database_discount(
            product.price,
            average_price,
        )
        parser_threshold = settings.effective_min_parser_discount
        database_threshold = settings.min_database_discount_percent

        if await self.is_warmup_period(session):
            if (
                parser_discount is not None
                and parser_discount >= parser_threshold
            ):
                return DiscountDecision(
                    action=DealAction.POST,
                    reason='warmup_parser_discount',
                    parser_discount=parser_discount,
                    database_discount=database_discount,
                    average_price=average_price,
                )
            return DiscountDecision(
                action=DealAction.SKIP,
                reason='warmup_below_parser_threshold',
                parser_discount=parser_discount,
                database_discount=database_discount,
                average_price=average_price,
            )

        db_ok = (
            database_discount is not None
            and database_discount >= database_threshold
        )
        parser_ok = (
            parser_discount is not None
            and parser_discount >= parser_threshold
        )

        if db_ok and parser_ok:
            return DiscountDecision(
                action=DealAction.POST,
                reason='db_and_parser_discount',
                parser_discount=parser_discount,
                database_discount=database_discount,
                average_price=average_price,
            )
        if db_ok and not parser_ok:
            return DiscountDecision(
                action=DealAction.POST_AVERAGE_NOTE,
                reason='db_discount_only',
                parser_discount=parser_discount,
                database_discount=database_discount,
                average_price=average_price,
            )
        if not db_ok and parser_ok:
            return DiscountDecision(
                action=DealAction.MODERATE,
                reason='parser_discount_needs_admin',
                parser_discount=parser_discount,
                database_discount=database_discount,
                average_price=average_price,
            )
        return DiscountDecision(
            action=DealAction.SKIP,
            reason='below_both_thresholds',
            parser_discount=parser_discount,
            database_discount=database_discount,
            average_price=average_price,
        )
