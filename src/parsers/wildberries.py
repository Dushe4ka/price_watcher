from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.utils import (
    BlockedError,
    NotFoundError,
    ParsingError,
    create_http_client,
    retry_request,
)
from src.parsers.wb_api import (
    build_image_url,
    calc_basket,
    extract_product_from_search,
    products_from_search_payload,
    wb_search_headers,
    wb_search_urls,
)

_PRODUCT_ID_RE = re.compile(r'wildberries\.ru/catalog/(\d+)')


class WildberriesParser(BaseParser):
    marketplace = 'wildberries'

    def extract_product_id(self, url: str) -> str:
        match = _PRODUCT_ID_RE.search(url)
        if not match:
            raise ValueError(
                f'Cannot extract Wildberries product ID from URL: {url}'
            )
        return match.group(1)

    def build_url(self, product_id: str) -> str:
        return (
            f'https://www.wildberries.ru/catalog/'
            f'{product_id}/detail.aspx'
        )

    @retry_request
    async def parse_product(self, url_or_id: str) -> ParsedProduct:
        if url_or_id.startswith('http') or 'wildberries.ru' in url_or_id:
            product_id = self.extract_product_id(url_or_id)
        else:
            product_id = url_or_id.strip()

        card = await self._fetch_card_json(product_id)

        title = card.get('imt_name', '') or card.get('nm_name', '')
        if not title:
            title = f'Wildberries #{product_id}'

        search_data = await self._search_for_price(product_id, title)

        if search_data:
            return search_data

        return ParsedProduct(
            external_id=product_id,
            title=title,
            price=Decimal(0),
            original_price=None,
            discount_percent=None,
            in_stock=False,
            image_url=build_image_url(product_id),
            product_url=self.build_url(product_id),
        )

    async def _fetch_card_json(
        self, product_id: str,
    ) -> dict[str, Any]:
        vol, part, basket = calc_basket(int(product_id))
        url = (
            f'https://basket-{basket}.wbbasket.ru'
            f'/vol{vol}/part{part}/{product_id}/info/ru/card.json'
        )
        async with create_http_client() as client:
            response = await client.get(url)
            if response.status_code == 403:
                raise BlockedError(
                    f'Wildberries blocked request for {product_id}'
                )
            if response.status_code == 404:
                raise NotFoundError(
                    f'Wildberries product {product_id} not found'
                )
            response.raise_for_status()
            try:
                return response.json()
            except Exception as exc:
                raise ParsingError(
                    f'Failed to decode JSON for WB {product_id}'
                ) from exc

    async def _search_for_price(
        self, product_id: str, title: str,
    ) -> ParsedProduct | None:
        query = title[:80]
        headers = wb_search_headers(query)
        async with create_http_client() as client:
            for url in wb_search_urls(query, page=1):
                try:
                    response = await client.get(url, headers=headers)
                except Exception:
                    continue
                if response.status_code != 200:
                    continue
                try:
                    data = response.json()
                except Exception:
                    continue

                for raw in products_from_search_payload(data):
                    parsed = extract_product_from_search(
                        raw, expected_id=product_id,
                    )
                    if parsed:
                        return parsed
        return None
