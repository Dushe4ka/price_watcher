from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.parsers.utils import create_http_client

logger = logging.getLogger(__name__)

_WB_CATALOG_API = (
    'https://catalog.wb.ru/catalog/{shard}/v2/catalog'
    '?appType=1&curr=rub&dest=-1257786&spp=30'
    '&cat={cat_id}&page={page}'
)
_WB_SEARCH_API = (
    'https://search.wb.ru/exactmatch/ru/common/v5/search'
    '?appType=1&curr=rub&dest=-1257786&spp=30'
    '&query={query}&page={page}'
)
_WB_SHARD_MAP = {
    'elektronika': 'electronic27',
    'krasota': 'beauty2',
    'dom-i-dacha': 'home23',
    'detyam': 'children',
    'obuv': 'catalog',
    'aksessuary': 'catalog',
}
_REQUEST_DELAY_SEC = 2.0
_RATE_LIMIT_RETRY_SEC = 6.0


class WildberriesCategoryCrawler(MarketplaceCrawler):
    marketplace = 'wildberries'

    async def crawl_category(
        self,
        crawl_url: str,
        category_slug: str,
        limit: int = 20,
    ) -> CategoryCrawlResult:
        product_ids: list[str] = []
        product_urls: list[str] = []

        parsed = urlparse(crawl_url)
        path_parts = [part for part in parsed.path.split('/') if part]
        query = None
        cat_id = None
        shard = 'catalog'

        if 'catalog' in path_parts:
            idx = path_parts.index('catalog')
            segments_after = path_parts[idx + 1:]
            if segments_after:
                if segments_after[0].isdigit():
                    cat_id = segments_after[0]
                else:
                    last_segment = segments_after[-1]
                    shard = _WB_SHARD_MAP.get(last_segment, 'catalog')
                    query = last_segment.replace('-', ' ')

        async with create_http_client() as client:
            page = 1
            while len(product_ids) < limit and page <= 3:
                if cat_id:
                    url = _WB_CATALOG_API.format(
                        shard=shard, cat_id=cat_id, page=page
                    )
                elif query:
                    url = _WB_SEARCH_API.format(
                        query=query.replace(' ', '%20'), page=page
                    )
                else:
                    url = _WB_SEARCH_API.format(
                        query=category_slug.replace('_', '%20'),
                        page=page,
                    )

                data = await self._fetch_page(client, url, category_slug)
                if data is None:
                    break

                products = (
                    data.get('data', {}).get('products')
                    or data.get('products')
                    or []
                )
                if not products:
                    break
                for product in products:
                    if len(product_ids) >= limit:
                        break
                    pid = str(product.get('id') or product.get('nmId', ''))
                    if pid and pid not in product_ids:
                        product_ids.append(pid)
                        product_urls.append(
                            f'https://www.wildberries.ru/catalog/'
                            f'{pid}/detail.aspx'
                        )
                page += 1
                await asyncio.sleep(_REQUEST_DELAY_SEC)

        logger.info(
            'WB crawl %s: found %s products',
            category_slug,
            len(product_ids),
        )
        return CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=category_slug,
            product_ids=product_ids[:limit],
            product_urls=product_urls[:limit],
        )

    async def _fetch_page(
        self,
        client: Any,
        url: str,
        category_slug: str,
    ) -> dict[str, Any] | None:
        for attempt in range(2):
            response = await client.get(url)
            if response.status_code == 429:
                logger.warning(
                    'WB rate limit for %s, retry in %ss',
                    category_slug,
                    _RATE_LIMIT_RETRY_SEC,
                )
                await asyncio.sleep(_RATE_LIMIT_RETRY_SEC)
                continue
            if response.status_code == 403:
                logger.warning('WB blocked category crawl: %s', category_slug)
                return None
            if response.status_code != 200:
                logger.warning(
                    'WB crawl %s HTTP %s',
                    category_slug,
                    response.status_code,
                )
                return None
            return response.json()
        return None
