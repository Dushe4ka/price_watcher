from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.parsers.base import ParsedProduct
from src.parsers.utils import create_http_client
from src.parsers.wb_api import (
    extract_product_from_search,
    products_from_search_payload,
    wb_search_headers,
    wb_search_urls,
)

logger = logging.getLogger(__name__)

_REQUEST_DELAY_SEC = 5.0
_QUERY_DELAY_SEC = 10.0
_RATE_LIMIT_RETRY_SEC = 35.0


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
        product_ids: list[str] = []
        product_urls: list[str] = []
        pre_parsed: dict[str, ParsedProduct] = {}

        queries = search_queries or [category_slug.replace('_', ' ')]

        per_query_limit = max(limit // len(queries), 5)

        async with create_http_client() as client:
            for qi, query in enumerate(queries):
                if len(product_ids) >= limit:
                    break
                remaining = limit - len(product_ids)
                q_limit = min(per_query_limit, remaining)

                collected = await self._search_query(
                    client, query, category_slug, q_limit,
                )
                for pid, parsed in collected.items():
                    if pid not in pre_parsed and len(product_ids) < limit:
                        product_ids.append(pid)
                        product_urls.append(
                            f'https://www.wildberries.ru/catalog/'
                            f'{pid}/detail.aspx'
                        )
                        pre_parsed[pid] = parsed

                if qi < len(queries) - 1:
                    await asyncio.sleep(_QUERY_DELAY_SEC)

        logger.info(
            'WB crawl %s: found %s products from %s queries',
            category_slug,
            len(product_ids),
            len(queries),
        )
        return CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=category_slug,
            product_ids=product_ids[:limit],
            product_urls=product_urls[:limit],
            pre_parsed=pre_parsed,
        )

    async def _search_query(
        self,
        client: Any,
        query: str,
        category_slug: str,
        limit: int,
    ) -> dict[str, ParsedProduct]:
        collected: dict[str, ParsedProduct] = {}
        page = 1
        max_pages = 2

        while len(collected) < limit and page <= max_pages:
            data = await self._fetch_page(
                client, query, page, category_slug,
            )
            if data is None:
                break

            products = products_from_search_payload(data)
            if not products:
                logger.warning(
                    'WB crawl %s: empty products for query=%r page=%s '
                    '(top keys=%s)',
                    category_slug,
                    query,
                    page,
                    list(data.keys()),
                )
                break

            before = len(collected)
            for raw in products:
                if len(collected) >= limit:
                    break
                parsed = extract_product_from_search(raw)
                if parsed and parsed.external_id not in collected:
                    collected[parsed.external_id] = parsed

            if len(collected) == before:
                break
            # Short / stub pages: avoid burning rate-limit budget on page 2.
            if len(products) <= 2:
                logger.info(
                    'WB crawl %s: short page (%s items) for %r — stop',
                    category_slug,
                    len(products),
                    query,
                )
                break

            page += 1
            await asyncio.sleep(_REQUEST_DELAY_SEC)

        return collected

    async def _fetch_page(
        self,
        client: Any,
        query: str,
        page: int,
        category_slug: str,
    ) -> dict[str, Any] | None:
        headers = wb_search_headers(query)
        urls = wb_search_urls(query, page)

        for attempt in range(3):
            saw_rate_limit = False
            for url in urls:
                try:
                    response = await client.get(url, headers=headers)
                except Exception as exc:
                    logger.warning(
                        'WB crawl %s network error: %s', category_slug, exc,
                    )
                    saw_rate_limit = True
                    continue

                if response.status_code == 429:
                    logger.warning(
                        'WB rate limit for %s via %s (attempt %s)',
                        category_slug,
                        url.split('?')[0],
                        attempt + 1,
                    )
                    saw_rate_limit = True
                    continue
                if response.status_code == 403:
                    logger.warning(
                        'WB host blocked for %s (%s), trying fallback',
                        category_slug,
                        url.split('?')[0],
                    )
                    continue
                if response.status_code != 200:
                    logger.warning(
                        'WB crawl %s HTTP %s (%s), trying fallback',
                        category_slug,
                        response.status_code,
                        url.split('?')[0],
                    )
                    continue
                try:
                    return response.json()
                except Exception:
                    logger.warning(
                        'WB crawl %s: invalid JSON from %s',
                        category_slug,
                        url.split('?')[0],
                    )
                    continue

            if not saw_rate_limit:
                break
            await asyncio.sleep(_RATE_LIMIT_RETRY_SEC)

        return None
