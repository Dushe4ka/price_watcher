from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from src.ozon.client import ozon_client
from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.utils import (
    BlockedError,
    NotFoundError,
    ParsingError,
    create_http_client,
    retry_request,
)

_PRODUCT_ID_RE = re.compile(r'ozon\.ru/product/.*?-(\d+)/?')
_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


class OzonParser(BaseParser):
    marketplace = 'ozon'

    def extract_product_id(self, url: str) -> str:
        match = _PRODUCT_ID_RE.search(url)
        if not match:
            raise ValueError(f'Cannot extract Ozon product ID from URL: {url}')
        return match.group(1)

    def build_url(self, product_id: str) -> str:
        return f'https://www.ozon.ru/product/{product_id}/'

    @retry_request
    async def parse_product(self, url_or_id: str) -> ParsedProduct:
        if url_or_id.startswith('http') or 'ozon.ru' in url_or_id:
            product_id = self.extract_product_id(url_or_id)
        else:
            product_id = url_or_id.strip()

        browser_data = await ozon_client.product_summary(product_id)
        if browser_data is not None:
            return browser_data

        try:
            return await self._parse_via_api(product_id)
        except NotFoundError:
            raise
        except Exception:
            return await self._parse_via_html(product_id)

    async def _parse_via_api(self, product_id: str) -> ParsedProduct:
        browser_data = await ozon_client.product_summary(product_id)
        if browser_data is not None:
            return browser_data
        raise ParsingError(
            f'Could not extract product data from Ozon API for {product_id}'
        )

    def _extract_from_api_payload(
        self,
        payload: dict[str, Any],
        product_id: str,
    ) -> ParsedProduct:
        title = ''
        price: Decimal | None = None
        original_price: Decimal | None = None
        image_url: str | None = None
        in_stock = True

        for raw_value in payload.get('widgetStates', {}).values():
            if isinstance(raw_value, str):
                try:
                    value = json.loads(raw_value)
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                value = raw_value
            if not isinstance(value, dict):
                continue

            web_price = value.get('webPrice') or value.get('price')
            if isinstance(web_price, dict):
                price = price or _parse_price_string(web_price.get('price'))
                original_price = original_price or _parse_price_string(
                    web_price.get('originalPrice')
                )
            if not title:
                title = value.get('title', '') or value.get(
                    'productTitle', ''
                )
            if not image_url:
                covers = (
                    value.get('coverImage')
                    or value.get('images')
                    or value.get('gallery')
                )
                if isinstance(covers, list) and covers:
                    first = covers[0]
                    image_url = (
                        first
                        if isinstance(first, str)
                        else first.get('src') or first.get('url')
                    )
                elif isinstance(covers, str):
                    image_url = covers
            if value.get('isOutOfStock') is True:
                in_stock = False

        if price is None:
            raise ParsingError(
                f'Could not extract price from Ozon API for {product_id}'
            )
        return ParsedProduct(
            external_id=product_id,
            title=title,
            price=price,
            original_price=original_price,
            discount_percent=self.calc_discount(price, original_price),
            in_stock=in_stock,
            image_url=image_url,
            product_url=self.build_url(product_id),
        )

    async def _parse_via_html(self, product_id: str) -> ParsedProduct:
        page_url = self.build_url(product_id)
        async with create_http_client() as client:
            response = await client.get(page_url)
            if response.status_code == 403:
                raise BlockedError(f'Ozon blocked HTML for {product_id}')
            if response.status_code == 404:
                raise NotFoundError(f'Ozon product {product_id} not found')
            response.raise_for_status()
            html = response.text

        for block in _JSON_LD_RE.findall(html):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get('@type') in ('Product', 'IndividualProduct'):
                    return self._extract_from_json_ld(item, product_id)
        raise ParsingError(f'No JSON-LD on Ozon page for {product_id}')

    def _extract_from_json_ld(
        self,
        item: dict[str, Any],
        product_id: str,
    ) -> ParsedProduct:
        title: str = item.get('name', '')
        offers: dict[str, Any] = item.get('offers', {})
        price = _parse_price_string(offers.get('price'))
        if price is None:
            price = _parse_price_string(offers.get('lowPrice'))
        if price is None:
            raise ParsingError(
                f'No price in Ozon JSON-LD for {product_id}'
            )
        original_price = _parse_price_string(offers.get('highPrice'))
        availability = offers.get('availability', '')
        in_stock = 'InStock' in availability if availability else True
        image_url: str | None = None
        image_raw = item.get('image')
        if isinstance(image_raw, list) and image_raw:
            image_url = image_raw[0]
        elif isinstance(image_raw, str):
            image_url = image_raw
        return ParsedProduct(
            external_id=product_id,
            title=title,
            price=price,
            original_price=original_price,
            discount_percent=self.calc_discount(price, original_price),
            in_stock=in_stock,
            image_url=image_url,
            product_url=self.build_url(product_id),
        )


_PRICE_CLEAN_RE = re.compile(r'[^\d.,]')


def _parse_price_string(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if not isinstance(raw, str):
        return None
    cleaned = _PRICE_CLEAN_RE.sub('', raw).replace(',', '.')
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
