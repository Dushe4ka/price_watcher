from __future__ import annotations

import asyncio
import logging

from src.core.config import settings
from src.parsers.base import ParsedProduct
from src.parsers.utils import BlockedError
from src.wb.constants import CATEGORY_CARDS_JS, DETAIL_PAGE_JS, build_product_url
from src.wb.dom_extract import card_to_parsed_product, detail_to_parsed_product
from src.wb.session import WBBrowserSession

logger = logging.getLogger(__name__)


class WBClient:
    def __init__(self, session: WBBrowserSession | None = None) -> None:
        self._session = session or WBBrowserSession()

    async def category_products(
        self,
        url: str,
        limit: int,
    ) -> tuple[list[str], dict[str, ParsedProduct]]:
        cards = await self._fetch(url, CATEGORY_CARDS_JS)
        if cards is None:
            return [], {}

        pre_parsed: dict[str, ParsedProduct] = {}
        for raw in cards:
            if len(pre_parsed) >= limit:
                break
            product = card_to_parsed_product(raw)
            if product is not None and product.external_id not in pre_parsed:
                pre_parsed[product.external_id] = product

        return list(pre_parsed.keys()), pre_parsed

    async def product_detail(self, product_id: str) -> ParsedProduct | None:
        raw = await self._fetch(build_product_url(product_id), DETAIL_PAGE_JS)
        if raw is None:
            raise BlockedError(
                f'WB antibot blocked product detail fetch for {product_id}'
            )
        return detail_to_parsed_product(raw, product_id)

    async def _fetch(self, url: str, extract_js: str):
        attempts = max(1, settings.wb_fetch_retries)
        last_block: BlockedError | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = await self._fetch_once(url, extract_js)
            except BlockedError as exc:
                last_block = exc
                self._session.note_block()
                logger.warning(
                    'WB blocked %s attempt %s/%s: %s', url, attempt, attempts, exc,
                )
                await self._session.close()
                if attempt < attempts:
                    try:
                        await self._session.rotate_and_restart()
                    except BlockedError as restart_exc:
                        last_block = restart_exc
                        break
                    await asyncio.sleep(settings.wb_request_delay_sec)
                continue

            self._session.note_success()
            return result

        if last_block is not None:
            logger.error('WB fetch failed for %s after retries: %s', url, last_block)
        return None

    async def _fetch_once(self, url: str, extract_js: str):
        page = await self._session.ensure_page()
        await self._session.goto_and_wait(page, url)
        await asyncio.sleep(settings.wb_request_delay_sec)
        return await page.evaluate(extract_js)

    async def close(self) -> None:
        await self._session.close()


wb_client = WBClient()
