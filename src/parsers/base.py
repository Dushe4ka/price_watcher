from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

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
