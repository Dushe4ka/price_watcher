from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from src.core.config import settings
from src.marketplaces.contracts import SourceOutcome
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode
from src.marketplaces.validation import ValidationState, validate_ozon_payload
from src.ozon.constants import (
    OZON_MOBILE_HEADERS,
    OZON_PAGE_JSON_URLS,
    OZON_SEARCH_PATH,
)
from src.ozon.parse_widgets import (
    extract_product_ids,
    extract_product_summary_map,
)
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
            raise MarketplaceSourceError(
                SourceOutcome.INVALID_CONFIG,
                SafeErrorCode.INVALID_CONFIG,
            )

        encoded_path = quote(path, safe='/')
        try:
            payload = await self._fetch_once(encoded_path)
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
        self._session.note_success()
        return payload

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
        self,
        encoded_path: str,
    ) -> dict[str, Any]:
        page = await self._session.ensure_page()
        observed_challenge = False
        observed_transport_error = False
        for template in OZON_PAGE_JSON_URLS:
            url = template.format(path=encoded_path)
            response = await page.context.request.get(
                url,
                headers=OZON_MOBILE_HEADERS,
            )
            status = response.status
            text = await response.text()
            if status == 429:
                raise MarketplaceSourceError(
                    SourceOutcome.RATE_LIMITED,
                    SafeErrorCode.RATE_LIMITED,
                )
            if status in (403, 307) or _looks_like_antibot(text):
                observed_challenge = True
                continue
            if status >= 500:
                observed_transport_error = True
                continue
            if status != 200:
                continue
            try:
                payload: dict[str, Any] = json.loads(text)
            except json.JSONDecodeError:
                continue
            state = validate_ozon_payload(payload)
            if state is ValidationState.CHALLENGE:
                observed_challenge = True
                continue
            if state is ValidationState.DRIFT:
                continue
            await asyncio.sleep(settings.ozon_request_delay_sec)
            return payload

        if observed_challenge:
            raise MarketplaceSourceError(
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_DETECTED,
            )
        if observed_transport_error:
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )
        raise MarketplaceSourceError(
            SourceOutcome.PARSE_DRIFT,
            SafeErrorCode.PARSE_DRIFT,
        )

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
