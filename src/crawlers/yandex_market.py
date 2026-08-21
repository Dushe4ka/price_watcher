from __future__ import annotations

import asyncio
import logging

import httpx

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.marketplaces.contracts import SourceOutcome
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode
from src.marketplaces.validation import ValidationState, validate_yandex_html
from src.parsers.utils import create_http_client, get_random_ua
from src.parsers.ym_api import (
    build_product_url,
    iter_ld_json_products,
    product_id_from_ld_json,
)

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

        try:
            async with create_http_client(headers=headers) as client:
                for page in range(1, _MAX_PAGES + 1):
                    if len(product_ids) >= limit:
                        break
                    page_url = (
                        crawl_url
                        if page == 1
                        else f'{crawl_url}{sep}page={page}'
                    )
                    response = await client.get(page_url)
                    _raise_for_status(response.status_code)
                    html = response.text
                    state = validate_yandex_html(html)
                    if state is ValidationState.CHALLENGE:
                        raise MarketplaceSourceError(
                            SourceOutcome.CHALLENGE,
                            SafeErrorCode.CHALLENGE_DETECTED,
                        )
                    if state is ValidationState.DRIFT:
                        raise MarketplaceSourceError(
                            SourceOutcome.PARSE_DRIFT,
                            SafeErrorCode.PARSE_DRIFT,
                        )
                    if state is ValidationState.VALID_EMPTY:
                        break

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
                        raise MarketplaceSourceError(
                            SourceOutcome.PARSE_DRIFT,
                            SafeErrorCode.PARSE_DRIFT,
                        )
                    if page < _MAX_PAGES and len(product_ids) < limit:
                        await asyncio.sleep(_PAGE_DELAY_SEC)
        except MarketplaceSourceError:
            raise
        except httpx.HTTPError as exc:
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
                cause=exc,
            ) from exc

        logger.info(
            'Yandex Market category crawl completed: item_count=%s',
            len(product_ids),
        )
        return CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=category_slug,
            product_ids=product_ids[:limit],
            product_urls=product_urls[:limit],
        )


def _raise_for_status(status_code: int) -> None:
    if status_code == 403:
        raise MarketplaceSourceError(
            SourceOutcome.CHALLENGE,
            SafeErrorCode.CHALLENGE_DETECTED,
        )
    if status_code == 429:
        raise MarketplaceSourceError(
            SourceOutcome.RATE_LIMITED,
            SafeErrorCode.RATE_LIMITED,
        )
    if status_code >= 500:
        raise MarketplaceSourceError(
            SourceOutcome.TRANSPORT_ERROR,
            SafeErrorCode.TRANSPORT_FAILED,
        )
    if status_code != 200:
        raise MarketplaceSourceError(
            SourceOutcome.PARSE_DRIFT,
            SafeErrorCode.PARSE_DRIFT,
        )
