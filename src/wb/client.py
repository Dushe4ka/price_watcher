from __future__ import annotations

import asyncio
import logging

from src.core.config import settings
from src.marketplaces.contracts import SourceOutcome
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode
from src.parsers.base import ParsedProduct
from src.parsers.utils import BlockedError
from src.wb.constants import (
    CATEGORY_CARDS_JS,
    DETAIL_PAGE_JS,
    build_product_url,
)
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
        if not isinstance(cards, list):
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )

        pre_parsed: dict[str, ParsedProduct] = {}
        for raw in cards:
            if len(pre_parsed) >= limit:
                break
            product = card_to_parsed_product(raw)
            if product is not None and product.external_id not in pre_parsed:
                pre_parsed[product.external_id] = product

        if cards and not pre_parsed:
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )

        return list(pre_parsed.keys()), pre_parsed

    async def product_detail(self, product_id: str) -> ParsedProduct | None:
        raw = await self._fetch(build_product_url(product_id), DETAIL_PAGE_JS)
        if not isinstance(raw, dict):
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )
        product = detail_to_parsed_product(raw, product_id)
        if product is None:
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )
        return product

    async def _fetch(self, url: str, extract_js: str):
        try:
            result = await self._fetch_once(url, extract_js)
        except MarketplaceSourceError:
            self._session.note_block()
            await self._session.close()
            raise
        except BlockedError as exc:
            self._session.note_block()
            await self._session.close()
            raise MarketplaceSourceError(
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_DETECTED,
                cause=exc,
            ) from exc
        except Exception as exc:
            await self._session.close()
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
                cause=exc,
            ) from exc
        if result is None:
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )
        else:
            self._session.note_success()
            return result

    async def _fetch_once(self, url: str, extract_js: str):
        page = await self._session.ensure_page()
        await self._session.goto_and_wait(page, url)
        await asyncio.sleep(settings.wb_request_delay_sec)
        return await page.evaluate(extract_js)

    async def close(self) -> None:
        await self._session.close()


wb_client = WBClient()
