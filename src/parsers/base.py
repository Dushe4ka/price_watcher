from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from src.marketplaces.contracts import MarketplaceName, MarketplaceResult

_DOMAIN_MAP: dict[str, str] = {
    'wildberries.ru': 'wildberries',
    'wb.ru': 'wildberries',
    'ozon.ru': 'ozon',
    'market.yandex.ru': 'yandex_market',
}


@dataclass(frozen=True, slots=True)
class ParsedProduct:
    external_id: str
    title: str
    price: Decimal
    original_price: Decimal | None = None
    discount_percent: int | None = None
    in_stock: bool = True
    image_url: str | None = None
    product_url: str | None = None
    rating: float | None = None
    review_count: int | None = None


class BaseParser(ABC):
    marketplace: ClassVar[str]

    @abstractmethod
    async def parse_product(self, url_or_id: str) -> ParsedProduct:
        ...

    @abstractmethod
    def extract_product_id(self, url: str) -> str:
        ...

    @abstractmethod
    def build_url(self, product_id: str) -> str:
        ...

    @staticmethod
    def detect_marketplace(url: str) -> str | None:
        url_lower = url.lower()
        for domain, marketplace in _DOMAIN_MAP.items():
            if domain in url_lower:
                return marketplace
        return None

    @staticmethod
    def calc_discount(
        price: Decimal,
        original_price: Decimal | None,
    ) -> int | None:
        if (
            original_price
            and original_price > 0
            and price < original_price
        ):
            return int((original_price - price) / original_price * 100)
        return None


async def parse_product_result(
    marketplace: MarketplaceName,
    product_id: str,
) -> MarketplaceResult[ParsedProduct]:
    """Parse one product over the configured marketplace source chain."""
    from src.marketplaces.contracts import ProductRequest
    from src.marketplaces.service import get_marketplace_service

    service = get_marketplace_service(marketplace)
    return await service.parse_product(ProductRequest(product_id))


async def parse_product(
    marketplace: MarketplaceName,
    product_id: str,
) -> ParsedProduct:
    """Unwrap a product parse into the historical exception contract."""
    from src.marketplaces.contracts import SourceOutcome
    from src.parsers.utils import NotFoundError, ParsingError

    result = await parse_product_result(marketplace, product_id)
    if result.outcome is SourceOutcome.SUCCESS and result.value is not None:
        return result.value
    if result.outcome in (SourceOutcome.NOT_FOUND, SourceOutcome.EMPTY):
        raise NotFoundError(f'{marketplace} product not found')
    raise ParsingError(
        f'{marketplace} parse failed: {result.outcome.value}',
    )
