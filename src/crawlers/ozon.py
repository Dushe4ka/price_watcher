from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import quote

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.parsers.utils import create_http_client

logger = logging.getLogger(__name__)

_OZON_PRODUCT_RE = re.compile(r'/product/(?:[^/]+-)?(\d+)')
_COMPOSER_API_TEMPLATES = (
    'https://api.ozon.ru/composer-api.bx/page/json/v2?url={path}',
    'https://www.ozon.ru/api/composer-api.bx/page/json/v2?url={path}',
)
_MOBILE_HEADERS = {
    'x-o3-app-name': 'ozonapp_android',
    'x-o3-app-version': '17.35.0',
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 14; SM-S918B) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Mobile Safari/537.36'
    ),
}


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

        payload = await self._fetch_category_payload(path, category_slug)
        if payload is None:
            return CategoryCrawlResult(
                marketplace=self.marketplace,
                category_slug=category_slug,
            )

        product_ids: list[str] = []
        product_urls: list[str] = []

        for raw_value in payload.get('widgetStates', {}).values():
            if len(product_ids) >= limit:
                break
            self._extract_from_widget(raw_value, product_ids, product_urls, limit)

        if not product_ids:
            for item in payload.get('layout', []):
                if len(product_ids) >= limit:
                    break
                self._walk_layout(item, product_ids, product_urls, limit)

        unique_ids = list(dict.fromkeys(product_ids))[:limit]
        unique_urls = list(dict.fromkeys(product_urls))[:limit]
        logger.info(
            'Ozon crawl %s: found %s products',
            category_slug,
            len(unique_ids),
        )
        return CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=category_slug,
            product_ids=unique_ids,
            product_urls=unique_urls,
        )

    async def _fetch_category_payload(
        self,
        path: str,
        category_slug: str,
    ) -> dict[str, Any] | None:
        encoded_path = quote(path, safe='/')
        async with create_http_client(headers=_MOBILE_HEADERS) as client:
            for template in _COMPOSER_API_TEMPLATES:
                api_url = template.format(path=encoded_path)
                try:
                    response = await client.get(api_url)
                except Exception as exc:
                    logger.warning(
                        'Ozon request failed %s/%s: %s',
                        category_slug,
                        api_url,
                        exc,
                    )
                    continue
                if response.status_code == 403:
                    logger.warning(
                        'Ozon blocked category %s via %s',
                        category_slug,
                        template.split('/')[2],
                    )
                    continue
                if response.status_code != 200:
                    continue
                try:
                    return response.json()
                except json.JSONDecodeError:
                    continue
        logger.warning(
            'Ozon category %s unavailable (blocked or empty)',
            category_slug,
        )
        return None

    def _extract_from_widget(
        self,
        raw_value: Any,
        product_ids: list[str],
        product_urls: list[str],
        limit: int,
    ) -> None:
        if isinstance(raw_value, str):
            try:
                value = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError):
                return
        else:
            value = raw_value
        self._walk_value(value, product_ids, product_urls, limit)

    def _walk_layout(
        self,
        item: Any,
        product_ids: list[str],
        product_urls: list[str],
        limit: int,
    ) -> None:
        if isinstance(item, dict):
            self._walk_value(item, product_ids, product_urls, limit)
            for child in item.get('placeholders', []):
                self._walk_layout(child, product_ids, product_urls, limit)
            for child in item.get('widgets', []):
                self._walk_layout(child, product_ids, product_urls, limit)

    def _walk_value(
        self,
        value: Any,
        product_ids: list[str],
        product_urls: list[str],
        limit: int,
    ) -> None:
        if len(product_ids) >= limit:
            return
        if isinstance(value, dict):
            for key in ('sku', 'productId', 'id'):
                raw_id = value.get(key)
                if raw_id and str(raw_id).isdigit():
                    pid = str(raw_id)
                    if pid not in product_ids:
                        product_ids.append(pid)
                        link = value.get('link') or value.get('action', {}).get(
                            'link', ''
                        )
                        if link:
                            product_urls.append(
                                f'https://www.ozon.ru{link}'
                                if link.startswith('/')
                                else link
                            )
            link = value.get('link') or value.get('href')
            if isinstance(link, str):
                match = _OZON_PRODUCT_RE.search(link)
                if match:
                    pid = match.group(1)
                    if pid not in product_ids:
                        product_ids.append(pid)
                        product_urls.append(
                            f'https://www.ozon.ru/product/{pid}/'
                        )
            for nested in value.values():
                self._walk_value(nested, product_ids, product_urls, limit)
        elif isinstance(value, list):
            for item in value:
                self._walk_value(item, product_ids, product_urls, limit)
        elif isinstance(value, str):
            for match in _OZON_PRODUCT_RE.finditer(value):
                pid = match.group(1)
                if pid not in product_ids and len(product_ids) < limit:
                    product_ids.append(pid)
