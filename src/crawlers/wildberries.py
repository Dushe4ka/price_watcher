from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.parsers.base import ParsedProduct
from src.parsers.utils import create_http_client

logger = logging.getLogger(__name__)

_WB_SEARCH_API = (
    'https://search.wb.ru/exactmatch/ru/common/v18/search'
    '?appType=1&curr=rub&dest=-1257786&lang=ru'
    '&resultset=catalog&sort=popular&spp=30'
    '&query={query}&page={page}'
)

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
            url = _WB_SEARCH_API.format(
                query=quote(query, safe=''),
                page=page,
            )
            data = await self._fetch_page(client, url, category_slug)
            if data is None:
                break

            products = data.get('products') or []
            if not products:
                break

            for raw in products:
                if len(collected) >= limit:
                    break
                parsed = _extract_product(raw)
                if parsed and parsed.external_id not in collected:
                    collected[parsed.external_id] = parsed

            page += 1
            await asyncio.sleep(_REQUEST_DELAY_SEC)

        return collected

    async def _fetch_page(
        self,
        client: Any,
        url: str,
        category_slug: str,
    ) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                response = await client.get(url)
            except Exception as exc:
                logger.warning(
                    'WB crawl %s network error: %s', category_slug, exc,
                )
                await asyncio.sleep(_RATE_LIMIT_RETRY_SEC)
                continue

            if response.status_code == 429:
                logger.warning(
                    'WB rate limit for %s, retry in %ss (attempt %s)',
                    category_slug,
                    _RATE_LIMIT_RETRY_SEC,
                    attempt + 1,
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
            try:
                return response.json()
            except Exception:
                logger.warning('WB crawl %s: invalid JSON', category_slug)
                return None
        return None


def _extract_product(raw: dict[str, Any]) -> ParsedProduct | None:
    pid = str(raw.get('id') or raw.get('nmId', ''))
    if not pid:
        return None

    name = raw.get('name', '')
    if not name:
        return None

    basic_price = 0
    sale_price = 0
    in_stock = bool(raw.get('totalQuantity', 0))

    for size in raw.get('sizes', []):
        price_info = size.get('price', {})
        if price_info:
            basic_price = price_info.get('basic', 0)
            sale_price = price_info.get('product', 0)
            break

    if not sale_price:
        return None

    price = Decimal(sale_price) / Decimal(100)
    original_price = Decimal(basic_price) / Decimal(100) if basic_price else None

    discount_percent: int | None = None
    if original_price and original_price > 0 and price < original_price:
        discount_percent = int(
            (original_price - price) / original_price * Decimal(100)
        )

    rating_raw = raw.get('reviewRating') or raw.get('rating')
    rating = float(rating_raw) if rating_raw is not None else None
    feedbacks = raw.get('feedbacks') or raw.get('nmFeedbacks')
    review_count = int(feedbacks) if feedbacks is not None else None

    return ParsedProduct(
        external_id=pid,
        title=name,
        price=price,
        original_price=original_price,
        discount_percent=discount_percent,
        in_stock=in_stock,
        image_url=_build_image_url(pid),
        product_url=f'https://www.wildberries.ru/catalog/{pid}/detail.aspx',
        rating=rating,
        review_count=review_count,
    )


def _build_image_url(product_id: str) -> str | None:
    try:
        pid = int(product_id)
    except ValueError:
        return None
    vol = pid // 100_000
    part = pid // 1_000
    if vol <= 143:
        basket = '01'
    elif vol <= 287:
        basket = '02'
    elif vol <= 431:
        basket = '03'
    elif vol <= 719:
        basket = '04'
    elif vol <= 1007:
        basket = '05'
    elif vol <= 1061:
        basket = '06'
    elif vol <= 1115:
        basket = '07'
    elif vol <= 1169:
        basket = '08'
    elif vol <= 1313:
        basket = '09'
    elif vol <= 1601:
        basket = '10'
    elif vol <= 1655:
        basket = '11'
    elif vol <= 1919:
        basket = '12'
    elif vol <= 2045:
        basket = '13'
    elif vol <= 2189:
        basket = '14'
    elif vol <= 2405:
        basket = '15'
    elif vol <= 2621:
        basket = '16'
    else:
        basket = '17'
    return (
        f'https://basket-{basket}.wbbasket.ru'
        f'/vol{vol}/part{part}/{pid}/images/big/1.webp'
    )
