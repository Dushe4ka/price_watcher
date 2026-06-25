from __future__ import annotations

import logging
import re

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.parsers.utils import BlockedError, create_http_client, get_random_ua

logger = logging.getLogger(__name__)

_YM_PRODUCT_RE = re.compile(
    r'(?:href=["\']|)(?:https://market\.yandex\.ru)?'
    r'/product(?:--[^"\']+)?/(\d+)'
)


class YandexMarketCategoryCrawler(MarketplaceCrawler):
    marketplace = 'yandex_market'

    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
    ) -> CategoryCrawlResult:
        headers = {
            'User-Agent': get_random_ua(),
            'Accept-Language': 'ru-RU,ru;q=0.9',
        }
        product_ids: list[str] = []
        product_urls: list[str] = []

        async with create_http_client(headers=headers) as client:
            response = await client.get(crawl_url)
            if response.status_code == 403:
                raise BlockedError(
                    f'Yandex Market blocked category crawl: {crawl_url}'
                )
            response.raise_for_status()
            html = response.text

        for match in _YM_PRODUCT_RE.finditer(html):
            if len(product_ids) >= limit:
                break
            pid = match.group(1)
            if pid not in product_ids:
                product_ids.append(pid)
                product_urls.append(
                    f'https://market.yandex.ru/product/{pid}'
                )

        logger.info(
            'YM crawl %s: found %s products',
            category_slug,
            len(product_ids),
        )
        return CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=category_slug,
            product_ids=product_ids[:limit],
            product_urls=product_urls[:limit],
        )
