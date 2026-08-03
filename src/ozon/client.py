from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from src.core.config import settings
from src.ozon.constants import OZON_COMPOSER_URLS, OZON_MOBILE_HEADERS, OZON_SEARCH_PATH
from src.ozon.parse_widgets import extract_product_ids, extract_product_summary_map
from src.ozon.session import OzonBrowserSession
from src.parsers.base import ParsedProduct
from src.parsers.utils import BlockedError

logger = logging.getLogger(__name__)


class OzonClient:
    def __init__(self, session: OzonBrowserSession | None = None) -> None:
        self._session = session or OzonBrowserSession()

    async def fetch_payload(self, path: str) -> dict[str, Any] | None:
        if not settings.ozon_enabled:
            return None
        if settings.ozon_proxy_required and not settings.proxies:
            logger.warning('Ozon proxy required but PROXY_LIST is empty')
            return None

        encoded_path = quote(path, safe='/')
        for template in OZON_COMPOSER_URLS:
            url = template.format(path=encoded_path)
            try:
                payload = await self._fetch_json_via_browser(url)
            except BlockedError as exc:
                logger.warning('Ozon blocked path %s: %s', path, exc)
                await self._session.close()
                continue
            if payload is not None:
                return payload
        return None

    async def search_product_ids(self, query: str, limit: int) -> list[str]:
        path = OZON_SEARCH_PATH.format(query=quote(query))
        payload = await self.fetch_payload(path)
        if payload is None:
            return []
        return extract_product_ids(payload, limit)

    async def category_products(
        self, path: str, limit: int,
    ) -> tuple[list[str], dict[str, ParsedProduct]]:
        payload = await self.fetch_payload(path)
        if payload is None:
            return [], {}
        pre_parsed = extract_product_summary_map(payload, limit=limit)
        product_ids = list(pre_parsed.keys())
        if not product_ids:
            product_ids = extract_product_ids(payload, limit)
        return product_ids[:limit], pre_parsed

    async def product_summary(self, product_id: str) -> ParsedProduct | None:
        payload = await self.fetch_payload(f'/product/{product_id}/')
        if payload is None:
            return None
        products = extract_product_summary_map(payload, limit=3)
        if product_id in products:
            return products[product_id]
        return next(iter(products.values()), None)

    async def _fetch_json_via_browser(self, url: str) -> dict[str, Any] | None:
        page = await self._session.ensure_page()
        response = await page.context.request.get(
            url,
            headers=OZON_MOBILE_HEADERS,
        )
        status = response.status
        if status in (403, 307):
            raise BlockedError(f'Ozon blocked browser request: HTTP {status}')
        if status != 200:
            return None
        text = await response.text()
        try:
            payload: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            return None
        await asyncio.sleep(settings.ozon_request_delay_sec)
        return payload


ozon_client = OzonClient()
