"""Shared Wildberries search API helpers.

WB search payloads vary by anti-bot / API generation:
- classic: top-level ``products`` with prices in ``sizes[].price``
- nested: ``data.products`` with ``salePriceU`` / ``priceU`` (kopecks)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import quote

from src.parsers.base import ParsedProduct

# Public search host first; frontend host as fallback.
_WB_SEARCH_HOSTS: tuple[str, ...] = (
    'https://search.wb.ru',
    'https://u-search.wb.ru',
)

_WB_SEARCH_PATH = (
    '/exactmatch/ru/common/v18/search'
    '?appType=1&curr=rub&dest=-1257786&lang=ru'
    '&resultset=catalog&sort=popular&spp=30'
    '&query={query}&page={page}'
)


def wb_search_urls(query: str, page: int = 1) -> list[str]:
    encoded = quote(query, safe='')
    path = _WB_SEARCH_PATH.format(query=encoded, page=page)
    return [f'{host}{path}' for host in _WB_SEARCH_HOSTS]


def wb_search_headers(query: str | None = None) -> dict[str, str]:
    referer = 'https://www.wildberries.ru/'
    if query:
        referer = (
            'https://www.wildberries.ru/catalog/0/search.aspx'
            f'?search={quote(query)}'
        )
    return {
        'Accept': '*/*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': 'https://www.wildberries.ru',
        'Referer': referer,
    }


def products_from_search_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return product dicts from either classic or nested WB search JSON."""
    products = data.get('products')
    if isinstance(products, list) and products:
        return products

    nested = data.get('data')
    if isinstance(nested, dict):
        nested_products = nested.get('products')
        if isinstance(nested_products, list):
            return nested_products

    if isinstance(products, list):
        return products
    return []


def extract_product_from_search(
    raw: dict[str, Any],
    *,
    expected_id: str | None = None,
) -> ParsedProduct | None:
    pid = str(raw.get('id') or raw.get('nmId', ''))
    if not pid:
        return None
    if expected_id is not None and pid != expected_id:
        return None

    name = raw.get('name', '')
    if not name and expected_id is None:
        return None

    basic_price, sale_price = _extract_prices_kopecks(raw)
    if not sale_price:
        return None

    price = Decimal(sale_price) / Decimal(100)
    original_price = (
        Decimal(basic_price) / Decimal(100) if basic_price else None
    )

    discount_percent: int | None = None
    if original_price and original_price > 0 and price < original_price:
        discount_percent = int(
            (original_price - price) / original_price * Decimal(100)
        )

    rating_raw = raw.get('reviewRating') or raw.get('rating')
    rating = float(rating_raw) if rating_raw is not None else None
    feedbacks = raw.get('feedbacks') or raw.get('nmFeedbacks')
    review_count = int(feedbacks) if feedbacks is not None else None
    quantity = raw.get('totalQuantity')
    in_stock = bool(quantity) if quantity is not None else True

    return ParsedProduct(
        external_id=pid,
        title=name or f'Wildberries #{pid}',
        price=price,
        original_price=original_price,
        discount_percent=discount_percent,
        in_stock=in_stock,
        image_url=build_image_url(pid),
        product_url=f'https://www.wildberries.ru/catalog/{pid}/detail.aspx',
        rating=rating,
        review_count=review_count,
    )


def _extract_prices_kopecks(raw: dict[str, Any]) -> tuple[int, int]:
    """Return (basic, sale) prices in kopecks from sizes or top-level fields."""
    basic_price = 0
    sale_price = 0

    for size in raw.get('sizes') or []:
        if not isinstance(size, dict):
            continue
        price_info = size.get('price') or {}
        if not isinstance(price_info, dict):
            continue
        product = price_info.get('product') or 0
        if product:
            sale_price = int(product)
            basic_price = int(price_info.get('basic') or 0)
            break

    if not sale_price:
        sale_raw = raw.get('salePriceU') or raw.get('priceU') or 0
        basic_raw = raw.get('priceU') or sale_raw or 0
        try:
            sale_price = int(sale_raw)
            basic_price = int(basic_raw)
        except (TypeError, ValueError):
            return 0, 0

    return basic_price, sale_price


def calc_basket(pid: int) -> tuple[int, int, str]:
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


def build_image_url(product_id: str) -> str | None:
    try:
        pid = int(product_id)
    except ValueError:
        return None
    vol, part, basket = calc_basket(pid)
    return (
        f'https://basket-{basket}.wbbasket.ru'
        f'/vol{vol}/part{part}/{pid}/images/big/1.webp'
    )
