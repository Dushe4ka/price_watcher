from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CategoryCrawlResult:
    marketplace: str
    category_slug: str
    product_ids: list[str] = field(default_factory=list)
    product_urls: list[str] = field(default_factory=list)


class MarketplaceCrawler(ABC):
    marketplace: str

    @abstractmethod
    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
    ) -> CategoryCrawlResult:
        ...
