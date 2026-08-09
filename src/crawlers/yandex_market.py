from __future__ import annotations

import asyncio
import logging

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.parsers.utils import BlockedError, create_http_client, get_random_ua
from src.parsers.ym_api import build_product_url, iter_ld_json_products, product_id_from_ld_json

logger = logging.getLogger(__name__)

_PAGE_DELAY_SEC = 1.0
_MAX_PAGES = 5


class YandexMarketCategoryCrawler(MarketplaceCrawler):
    marketplace = 'yandex_market'

    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
        *,
        search_queries: list[str] | None = None,
    ) -> CategoryCrawlResult:
        headers = {
            'User-Agent': get_random_ua(),
            'Accept-Language': 'ru-RU,ru;q=0.9',
        }
        product_ids: list[str] = []
        product_urls: list[str] = []
        sep = '&' if '?' in crawl_url else '?'

        async with create_http_client(headers=headers) as client:
            for page in range(1, _MAX_PAGES + 1):
                if len(product_ids) >= limit:
                    break
                page_url = crawl_url if page == 1 else f'{crawl_url}{sep}page={page}'
                response = await client.get(page_url)
                if response.status_code == 403:
                    raise BlockedError(
                        f'Yandex Market blocked category crawl: {crawl_url}'
                    )
                if response.status_code != 200:
                    break
                html = response.text

                before = len(product_ids)
                for item in iter_ld_json_products(html):
                    if len(product_ids) >= limit:
                        break
                    product_id = product_id_from_ld_json(item)
                    if product_id is None or product_id in product_ids:
                        continue
                    product_ids.append(product_id)
                    product_urls.append(build_product_url(product_id))

                if len(product_ids) == before:
                    break
                if page < _MAX_PAGES and len(product_ids) < limit:
                    await asyncio.sleep(_PAGE_DELAY_SEC)

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
