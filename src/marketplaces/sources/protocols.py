from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    CategoryRequest,
    ProductRequest,
    SearchRequest,
    SourceResult,
)
from src.marketplaces.errors import MarketplaceSourceError
from src.parsers.base import ParsedProduct


@runtime_checkable
class CategorySource(Protocol):
    async def crawl_category(
        self,
        request: CategoryRequest,
    ) -> SourceResult[CategoryCrawlResult]:
        ...


@runtime_checkable
class ProductSource(Protocol):
    async def parse_product(
        self,
        request: ProductRequest,
    ) -> SourceResult[ParsedProduct]:
        ...


@runtime_checkable
class SearchSource(Protocol):
    async def search_products(
        self,
        request: SearchRequest,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        ...


__all__ = (
    'CategorySource',
    'MarketplaceSourceError',
    'ProductSource',
    'SearchSource',
)
