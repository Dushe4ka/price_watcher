from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from selectolax.parser import HTMLParser

from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.utils import (
    BlockedError,
    NotFoundError,
    ParsingError,
    create_http_client,
    get_random_ua,
    retry_request,
)

_PRODUCT_ID_RE = re.compile(
    r'market\.yandex\.ru/product(?:--[^/]+)?/(\d+)'
)


class YandexMarketParser(BaseParser):
    marketplace = 'yandex_market'

    def extract_product_id(self, url: str) -> str:
        match = _PRODUCT_ID_RE.search(url)
        if not match:
            raise ValueError(
                f'Cannot extract Yandex Market product ID from URL: {url}'
            )
        return match.group(1)

    def build_url(self, product_id: str) -> str:
        return f'https://market.yandex.ru/product/{product_id}'

    @retry_request
    async def parse_product(self, url_or_id: str) -> ParsedProduct:
        if url_or_id.startswith('http') or 'market.yandex.ru' in url_or_id:
            product_id = self.extract_product_id(url_or_id)
        else:
            product_id = url_or_id.strip()

        page_url = self.build_url(product_id)
        headers = {
            'User-Agent': get_random_ua(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        async with create_http_client(headers=headers) as client:
            response = await client.get(page_url)
            if response.status_code == 403:
                raise BlockedError(
                    f'Yandex Market blocked request for {product_id}'
                )
            if response.status_code == 404:
                raise NotFoundError(
                    f'Yandex Market product {product_id} not found'
                )
            response.raise_for_status()
            html = response.text

        tree = HTMLParser(html)
        for node in tree.css('script[type="application/ld+json"]'):
            raw_text = node.text(strip=True)
            if not raw_text:
                continue
            try:
                data: Any = json.loads(raw_text)
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get('@type') in ('Product', 'IndividualProduct'):
                    return self._extract_from_json_ld(item, product_id)

        raise ParsingError(
            f'No Product JSON-LD on Yandex Market page for {product_id}'
        )

    def _extract_from_json_ld(
        self,
        item: dict[str, Any],
        product_id: str,
    ) -> ParsedProduct:
        title: str = item.get('name', '')
        offers_raw: Any = item.get('offers', {})
        if isinstance(offers_raw, dict):
            offers_list = [offers_raw]
        elif isinstance(offers_raw, list):
            offers_list = offers_raw
        else:
            offers_list = []

        price: Decimal | None = None
        original_price: Decimal | None = None
        in_stock = True

        for offer in offers_list:
            price = price or _parse_price_value(offer.get('price'))
            original_price = original_price or _parse_price_value(
                offer.get('highPrice')
            )
            availability = offer.get('availability', '')
            if availability and 'InStock' not in availability:
                in_stock = False

        if price is None:
            raise ParsingError(
                f'No price in Yandex Market JSON-LD for {product_id}'
            )

        image_url: str | None = None
        image_raw = item.get('image')
        if isinstance(image_raw, list) and image_raw:
            first = image_raw[0]
            image_url = first if isinstance(first, str) else first.get('url')
        elif isinstance(image_raw, str):
            image_url = image_raw

        rating: float | None = None
        review_count: int | None = None
        agg = item.get('aggregateRating')
        if isinstance(agg, dict):
            rv = agg.get('ratingValue')
            if rv is not None:
                try:
                    rating = float(rv)
                except (ValueError, TypeError):
                    pass
            rc = agg.get('reviewCount') or agg.get('ratingCount')
            if rc is not None:
                try:
                    review_count = int(rc)
                except (ValueError, TypeError):
                    pass

        return ParsedProduct(
            external_id=product_id,
            title=title,
            price=price,
            original_price=original_price,
            discount_percent=self.calc_discount(price, original_price),
            in_stock=in_stock,
            image_url=image_url,
            product_url=self.build_url(product_id),
            rating=rating,
            review_count=review_count,
        )


_PRICE_CLEAN_RE = re.compile(r'[^\d.,]')


def _parse_price_value(raw: Any) -> Decimal | None:
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
