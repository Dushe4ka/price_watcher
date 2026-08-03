from __future__ import annotations

import logging

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.ozon.client import ozon_client

logger = logging.getLogger(__name__)


class OzonCategoryCrawler(MarketplaceCrawler):
    marketplace = 'ozon'

    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
        *,
        search_queries: list[str] | None = None,
    ) -> CategoryCrawlResult:
        path = crawl_url if crawl_url.startswith('/') else (
            '/' + crawl_url.split('ozon.ru', 1)[-1].lstrip('/')
        )
        if not path.endswith('/'):
            path += '/'

        product_ids, pre_parsed = await ozon_client.category_products(path, limit)
        product_urls = [
            f'https://www.ozon.ru/product/{pid}/'
            for pid in product_ids
        ]
        logger.info(
            'Ozon crawl %s: found %s products',
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
