from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from src.core.config import settings
from src.ozon.constants import OZON_MOBILE_HEADERS, OZON_PAGE_JSON_URLS, OZON_SEARCH_PATH
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
        last_block: BlockedError | None = None
        attempts = max(1, settings.ozon_fetch_retries)

        for attempt in range(1, attempts + 1):
            try:
                payload = await self._fetch_once(encoded_path, path)
            except BlockedError as exc:
                last_block = exc
                self._session.note_block()
                logger.warning(
                    'Ozon blocked path %s attempt %s/%s: %s',
                    path,
                    attempt,
                    attempts,
                    exc,
                )
                await self._session.close()
                if attempt < attempts:
                    try:
                        await self._session.rotate_and_restart()
                    except BlockedError as restart_exc:
                        last_block = restart_exc
                        break
                    await asyncio.sleep(settings.ozon_request_delay_sec)
                continue

            if payload is not None:
                self._session.note_success()
                return payload

        if last_block is not None:
            logger.error('Ozon fetch failed for %s after retries: %s', path, last_block)
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

    async def _fetch_once(
        self, encoded_path: str, path: str,
    ) -> dict[str, Any] | None:
        page = await self._session.ensure_page()
        for template in OZON_PAGE_JSON_URLS:
            url = template.format(path=encoded_path)
            response = await page.context.request.get(
                url,
                headers=OZON_MOBILE_HEADERS,
            )
            status = response.status
            text = await response.text()
            if status in (403, 307) or _looks_like_antibot(text):
                logger.debug(
                    'Ozon endpoint blocked %s status=%s path=%s',
                    template.split('/api/')[-1].split('.bx')[0],
                    status,
                    path,
                )
                continue
            if status != 200:
                continue
            try:
                payload: dict[str, Any] = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not payload.get('widgetStates') and not payload.get('layout'):
                continue
            await asyncio.sleep(settings.ozon_request_delay_sec)
            return payload

        raise BlockedError(f'Ozon blocked all page/json endpoints for {path}')

    async def close(self) -> None:
        await self._session.close()


def _looks_like_antibot(text: str) -> bool:
    head = text[:2000].lower()
    return (
        'antibot' in head
        or 'incidentid' in head
        or 'нет соединения' in head
        or 'нет\xa0соединения' in head
    )


ozon_client = OzonClient()
