from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.marketplaces.contracts import MarketplaceName, MarketplaceResult
    from src.parsers.base import ParsedProduct


@dataclass(frozen=True, slots=True)
class CategoryCrawlResult:
    marketplace: str
    category_slug: str
    product_ids: list[str] = field(default_factory=list)
    product_urls: list[str] = field(default_factory=list)
    pre_parsed: dict[str, ParsedProduct] = field(default_factory=dict)


class MarketplaceCrawler(ABC):
    marketplace: str

    @abstractmethod
    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
        *,
        search_queries: list[str] | None = None,
    ) -> CategoryCrawlResult:
        ...


async def crawl_category_result(
    marketplace: MarketplaceName,
    category_slug: str,
    limit: int = 20,
) -> MarketplaceResult[CategoryCrawlResult]:
    """Crawl one trusted category slug over the configured source chain."""
    from src.marketplaces.contracts import CategoryRequest
    from src.marketplaces.service import get_marketplace_service

    service = get_marketplace_service(marketplace)
    return await service.crawl_category(
        CategoryRequest(category_slug=category_slug, limit=limit),
    )


async def crawl_category(
    marketplace: MarketplaceName,
    category_slug: str,
    limit: int = 20,
) -> CategoryCrawlResult:
    """Unwrap a category crawl for call sites that need only the payload."""
    from src.marketplaces.contracts import SourceOutcome
    from src.marketplaces.errors import (
        MarketplaceOperationError,
        SafeErrorCode,
    )

    result = await crawl_category_result(marketplace, category_slug, limit)
    if result.outcome is SourceOutcome.SUCCESS and result.value is not None:
        return result.value
    if result.outcome is SourceOutcome.EMPTY:
        return CategoryCrawlResult(
            marketplace=marketplace,
            category_slug=category_slug,
        )
    error_code = next(
        (
            attempt.error_code
            for attempt in reversed(result.attempts)
            if attempt.error_code is not None
        ),
        SafeErrorCode.TRANSPORT_FAILED,
    )
    raise MarketplaceOperationError(
        marketplace,
        result.operation,
        error_code,
        result.attempts,
    )
