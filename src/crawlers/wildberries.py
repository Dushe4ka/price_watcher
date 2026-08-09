from __future__ import annotations

import logging

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.wb.client import wb_client

logger = logging.getLogger(__name__)


class WildberriesCategoryCrawler(MarketplaceCrawler):
    marketplace = 'wildberries'

    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
        *,
        search_queries: list[str] | None = None,
    ) -> CategoryCrawlResult:
        product_ids, pre_parsed = await wb_client.category_products(
            crawl_url, limit,
        )
        product_urls = [
            pre_parsed[pid].product_url or ''
            for pid in product_ids
        ]

        logger.info(
            'WB crawl %s: found %s products',
            category_slug,
            len(product_ids),
        )
        return CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=category_slug,
            product_ids=product_ids,
            product_urls=product_urls,
            pre_parsed=pre_parsed,
        )
