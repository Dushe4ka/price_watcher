from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.utils import (
    BlockedError,
    NotFoundError,
    ParsingError,
    create_http_client,
    retry_request,
)

_WB_SEARCH_API = (
    'https://search.wb.ru/exactmatch/ru/common/v18/search'
    '?appType=1&curr=rub&dest=-1257786&lang=ru'
    '&resultset=catalog&sort=popular&spp=30'
    '&query={query}&page=1'
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
            image_url=_build_image_url(product_id),
            product_url=self.build_url(product_id),
        )

    async def _fetch_card_json(
        self, product_id: str,
    ) -> dict[str, Any]:
        vol, part, basket = _calc_basket(int(product_id))
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
        url = _WB_SEARCH_API.format(query=quote(title[:80], safe=''))
        async with create_http_client() as client:
            try:
                response = await client.get(url)
            except Exception:
                return None
            if response.status_code != 200:
                return None
            try:
                data = response.json()
            except Exception:
                return None

        for raw in data.get('products', []):
            if str(raw.get('id', '')) == product_id:
                return _extract_from_search(raw, product_id)

        return None


def _extract_from_search(
    raw: dict[str, Any], product_id: str,
) -> ParsedProduct | None:
    name = raw.get('name', '')
    basic_price = 0
    sale_price = 0

    for size in raw.get('sizes', []):
        price_info = size.get('price', {})
        if price_info:
            basic_price = price_info.get('basic', 0)
            sale_price = price_info.get('product', 0)
            break

    if not sale_price:
        return None

    price = Decimal(sale_price) / Decimal(100)
    original_price = Decimal(basic_price) / Decimal(100) if basic_price else None

    discount_percent: int | None = None
    if original_price and original_price > 0 and price < original_price:
        discount_percent = int(
            (original_price - price) / original_price * Decimal(100)
        )

    in_stock = bool(raw.get('totalQuantity', 0))

    rating_raw = raw.get('reviewRating') or raw.get('rating')
    rating = float(rating_raw) if rating_raw is not None else None
    feedbacks = raw.get('feedbacks') or raw.get('nmFeedbacks')
    review_count = int(feedbacks) if feedbacks is not None else None

    return ParsedProduct(
        external_id=product_id,
        title=name or f'Wildberries #{product_id}',
        price=price,
        original_price=original_price,
        discount_percent=discount_percent,
        in_stock=in_stock,
        image_url=_build_image_url(product_id),
        product_url=(
            f'https://www.wildberries.ru/catalog/{product_id}/detail.aspx'
        ),
        rating=rating,
        review_count=review_count,
    )


def _calc_basket(pid: int) -> tuple[int, int, str]:
    vol = pid // 100_000
    part = pid // 1_000
    if vol <= 143:
        basket = '01'
    elif vol <= 287:
        basket = '02'
    elif vol <= 431:
        basket = '03'
    elif vol <= 719:
        basket = '04'
    elif vol <= 1007:
        basket = '05'
    elif vol <= 1061:
        basket = '06'
    elif vol <= 1115:
        basket = '07'
    elif vol <= 1169:
        basket = '08'
    elif vol <= 1313:
        basket = '09'
    elif vol <= 1601:
        basket = '10'
    elif vol <= 1655:
        basket = '11'
    elif vol <= 1919:
        basket = '12'
    elif vol <= 2045:
        basket = '13'
    elif vol <= 2189:
        basket = '14'
    elif vol <= 2405:
        basket = '15'
    elif vol <= 2621:
        basket = '16'
    else:
        basket = '17'
    return vol, part, basket


def _build_image_url(product_id: str) -> str | None:
    try:
        pid = int(product_id)
    except ValueError:
        return None
    vol, part, basket = _calc_basket(pid)
    return (
        f'https://basket-{basket}.wbbasket.ru'
        f'/vol{vol}/part{part}/{pid}/images/big/1.webp'
    )
