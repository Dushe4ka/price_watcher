from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from src.core.config import settings
from src.parsers.base import ParsedProduct
from src.services.market_search import build_search_query, fetch_market_prices

logger = logging.getLogger(__name__)


class MarketCheckStatus(StrEnum):
    SKIPPED = 'skipped'
    PASSED = 'passed'
    FAILED = 'failed'
    INCONCLUSIVE = 'inconclusive'


@dataclass(frozen=True, slots=True)
class MarketCheckResult:
    required: bool
    status: MarketCheckStatus
    reason: str = ''
    search_query: str = ''
    market_min_price: Decimal | None = None
    market_avg_price: Decimal | None = None
    market_discount_percent: int | None = None
    compared_marketplaces: tuple[str, ...] = ()


class MarketPriceChecker:
    @staticmethod
    def is_required(product: ParsedProduct, category_slug: str) -> bool:
        if product.price < Decimal(settings.market_check_min_price):
            return False
        return category_slug in settings.market_check_category_slugs

    @staticmethod
    def calc_market_discount(
        current_price: Decimal,
        market_price: Decimal | None,
    ) -> int | None:
        if market_price is None or market_price <= 0:
            return None
        if current_price >= market_price:
            return None
        return int((market_price - current_price) / market_price * 100)

    async def check(
        self,
        product: ParsedProduct,
        source_marketplace: str,
        category_slug: str,
    ) -> MarketCheckResult:
        if not self.is_required(product, category_slug):
            return MarketCheckResult(
                required=False,
                status=MarketCheckStatus.SKIPPED,
                reason='market_check_not_required',
            )

        search_query = build_search_query(product.title)
        if not search_query.strip():
            return MarketCheckResult(
                required=True,
                status=MarketCheckStatus.INCONCLUSIVE,
                reason='market_check_empty_query',
                search_query=search_query,
            )

        prices, marketplaces = await fetch_market_prices(
            product,
            source_marketplace,
            search_query,
        )
        if not prices:
            logger.info(
                'Market check inconclusive for %s (%s)',
                product.title[:60],
                search_query,
            )
            return MarketCheckResult(
                required=True,
                status=MarketCheckStatus.INCONCLUSIVE,
                reason='market_check_no_data',
                search_query=search_query,
            )

        market_min = min(prices)
        market_avg = (
            sum(prices, Decimal(0)) / Decimal(len(prices))
        ).quantize(Decimal('0.01'))
        market_discount = self.calc_market_discount(product.price, market_min)
        threshold = settings.market_check_discount_percent

        if market_discount is not None and market_discount >= threshold:
            return MarketCheckResult(
                required=True,
                status=MarketCheckStatus.PASSED,
                reason='market_check_passed',
                search_query=search_query,
                market_min_price=market_min,
                market_avg_price=market_avg,
                market_discount_percent=market_discount,
                compared_marketplaces=tuple(marketplaces),
            )

        return MarketCheckResult(
            required=True,
            status=MarketCheckStatus.FAILED,
            reason='not_cheaper_than_market',
            search_query=search_query,
            market_min_price=market_min,
            market_avg_price=market_avg,
            market_discount_percent=market_discount,
            compared_marketplaces=tuple(marketplaces),
        )
