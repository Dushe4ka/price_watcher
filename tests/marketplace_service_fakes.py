"""Shared stubs for composition-root tests without browser or network use."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    MarketplaceName,
    SourceName,
    SourceOutcome,
    SourceResult,
    source_empty,
    source_failure,
    source_success,
)
from src.marketplaces.errors import SafeErrorCode
from src.parsers.base import ParsedProduct


def parsed_product(
    external_id: str = '9000001',
    *,
    title: str = 'Synthetic Item',
    price: str = '1000',
    in_stock: bool = True,
) -> ParsedProduct:
    """Build one synthetic product; no live marketplace data is involved."""
    return ParsedProduct(
        external_id=external_id,
        title=title,
        price=Decimal(price),
        in_stock=in_stock,
        product_url=f'https://www.ozon.ru/product/{external_id}/',
    )


def crawl_result(
    marketplace: MarketplaceName = 'ozon',
    *,
    category_slug: str = 'beauty',
    product_ids: Sequence[str] = ('9000001',),
) -> CategoryCrawlResult:
    """Build one synthetic category crawl payload."""
    products = {
        product_id: parsed_product(product_id)
        for product_id in product_ids
    }
    return CategoryCrawlResult(
        marketplace=marketplace,
        category_slug=category_slug,
        product_ids=list(product_ids),
        product_urls=[
            products[product_id].product_url or ''
            for product_id in product_ids
        ],
        pre_parsed=products,
    )


def success(source: SourceName, value: Any) -> SourceResult[Any]:
    """Return one successful source result."""
    return source_success(source, value)


def empty(source: SourceName) -> SourceResult[Any]:
    """Return one structurally validated empty source result."""
    return source_empty(source)


def challenge(source: SourceName) -> SourceResult[Any]:
    """Return one challenge failure result."""
    return source_failure(
        source,
        SourceOutcome.CHALLENGE,
        SafeErrorCode.CHALLENGE_DETECTED,
    )


class StubSource:
    """Source adapter returning scripted results per marketplace operation."""

    def __init__(
        self,
        source: SourceName,
        *results: SourceResult[Any],
        crawl: Sequence[SourceResult[Any]] = (),
        product: Sequence[SourceResult[Any]] = (),
        search: Sequence[SourceResult[Any]] = (),
    ) -> None:
        self.source = source
        self.requests: list[Any] = []
        self._default = list(results)
        self._scripts = {
            'crawl_category': list(crawl),
            'parse_product': list(product),
            'search_products': list(search),
        }

    async def crawl_category(self, request: Any) -> SourceResult[Any]:
        return self._next('crawl_category', request)

    async def parse_product(self, request: Any) -> SourceResult[Any]:
        return self._next('parse_product', request)

    async def search_products(self, request: Any) -> SourceResult[Any]:
        return self._next('search_products', request)

    def _next(self, operation: str, request: Any) -> SourceResult[Any]:
        self.requests.append(request)
        results = self._scripts[operation] or self._default
        if not results:
            raise AssertionError('stub source ran out of scripted results')
        if len(results) == 1:
            return results[0]
        return results.pop(0)


class StubRegistry:
    """Registry stub exposing a fixed chain and a counted close."""

    def __init__(
        self,
        chain: Sequence[tuple[SourceName, StubSource]],
        *,
        start_error: Exception | None = None,
    ) -> None:
        self._chain = tuple(chain)
        self._start_error = start_error
        self.close_calls = 0
        self.start_calls = 0
        self.refresh_calls = 0
        self.closed = False

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError('registry is closed')
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error

    def refresh_category_urls(self) -> None:
        if self.closed:
            raise RuntimeError('registry is closed')
        self.refresh_calls += 1

    def sources_for(
        self,
        marketplace: MarketplaceName,
    ) -> tuple[tuple[SourceName, StubSource], ...]:
        del marketplace
        if self.closed:
            raise RuntimeError('registry is closed')
        return self._chain

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_calls += 1
