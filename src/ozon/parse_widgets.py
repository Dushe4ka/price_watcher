from __future__ import annotations

import json
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from src.ozon.constants import OZON_PRODUCT_RE
from src.parsers.base import ParsedProduct


def iter_widget_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_value in payload.get('widgetStates', {}).values():
        if isinstance(raw_value, str):
            try:
                value = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError):
                continue
        else:
            value = raw_value
        if isinstance(value, dict):
            result.append(value)
            result.extend(_nested_dicts(value))
    return result


def _nested_dicts(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 4 or not isinstance(node, (dict, list)):
        return []
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        for key in ('items', 'products', 'tiles', 'skuList', 'searchResults'):
            child = node.get(key)
            if isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        found.append(item)
                        found.extend(_nested_dicts(item, depth + 1))
            elif isinstance(child, dict):
                found.append(child)
                found.extend(_nested_dicts(child, depth + 1))
    return found


def extract_product_ids(payload: dict[str, Any], limit: int) -> list[str]:
    product_ids: list[str] = []
    for raw_value in payload.get('widgetStates', {}).values():
        if len(product_ids) >= limit:
            break
        text = raw_value if isinstance(raw_value, str) else json.dumps(raw_value)
        for match in OZON_PRODUCT_RE.finditer(text):
            product_id = match.group(1)
            if product_id not in product_ids:
                product_ids.append(product_id)
            if len(product_ids) >= limit:
                break
    return product_ids[:limit]


def extract_product_summary_map(
    payload: dict[str, Any],
    *,
    limit: int,
) -> dict[str, ParsedProduct]:
    result: dict[str, ParsedProduct] = {}

    for value in iter_widget_dicts(payload):
        if len(result) >= limit:
            break
        parsed = _parse_tile(value)
        if parsed is None:
            continue
        if parsed.external_id in result:
            continue
        result[parsed.external_id] = parsed

    if len(result) >= limit:
        return dict(list(result.items())[:limit])

    # Fallback: IDs from raw text + best-effort field scrape per id.
    for product_id in extract_product_ids(payload, limit):
        if len(result) >= limit:
            break
        if product_id in result:
            continue
        candidate = _extract_single_product(payload, product_id)
        if candidate is not None:
            result[product_id] = candidate
    return result


def _parse_tile(value: dict[str, Any]) -> ParsedProduct | None:
    product_id = _tile_product_id(value)
    if not product_id:
        return None

    title = (
        value.get('title')
        or value.get('productTitle')
        or value.get('name')
        or ''
    )
    if isinstance(title, dict):
        title = title.get('text') or title.get('value') or ''

    price, original_price = _tile_prices(value)
    if price is None:
        return None

    image_url = _tile_image(value)
    in_stock = value.get('isOutOfStock') is not True
    rating = _as_float(value.get('rating') or value.get('reviewsRating'))
    review_count = _as_int(
        value.get('reviewsCount')
        or value.get('feedbacks')
        or value.get('reviews')
    )

    return ParsedProduct(
        external_id=product_id,
        title=str(title) or f'Ozon #{product_id}',
        price=price,
        original_price=original_price,
        discount_percent=(
            int((original_price - price) / original_price * 100)
            if original_price and original_price > 0 and price < original_price
            else None
        ),
        in_stock=in_stock,
        image_url=image_url,
        product_url=f'https://www.ozon.ru/product/{product_id}/',
        rating=rating,
        review_count=review_count,
    )


def _tile_product_id(value: dict[str, Any]) -> str | None:
    for key in ('skuId', 'sku', 'id', 'productId'):
        raw = value.get(key)
        if isinstance(raw, int):
            return str(raw)
        if isinstance(raw, str) and raw.isdigit():
            return raw

    link = value.get('link') or value.get('action', {}).get('link')
    if isinstance(link, dict):
        link = link.get('link') or link.get('url')
    if isinstance(link, str):
        match = OZON_PRODUCT_RE.search(link)
        if match:
            return match.group(1)
    return None


def _tile_prices(
    value: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None]:
    price: Decimal | None = None
    original_price: Decimal | None = None

    web_price = value.get('webPrice') or value.get('price')
    if isinstance(web_price, dict):
        price = parse_price_string(
            web_price.get('price')
            or web_price.get('cardPrice')
            or web_price.get('totalPrice')
        )
        original_price = parse_price_string(
            web_price.get('originalPrice') or web_price.get('priceWithoutDiscount')
        )
    elif web_price is not None and not isinstance(web_price, (dict, list)):
        price = parse_price_string(web_price)

    if price is None:
        price = parse_price_string(value.get('finalPrice') or value.get('salePrice'))
    if original_price is None:
        original_price = parse_price_string(
            value.get('originalPrice') or value.get('priceWithoutDiscount')
        )
    return price, original_price


def _tile_image(value: dict[str, Any]) -> str | None:
    covers = (
        value.get('coverImage')
        or value.get('images')
        or value.get('gallery')
        or value.get('image')
    )
    if isinstance(covers, list) and covers:
        first = covers[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get('src') or first.get('url')
    if isinstance(covers, str):
        return covers
    if isinstance(covers, dict):
        return covers.get('src') or covers.get('url')
    return None


def _extract_single_product(
    payload: dict[str, Any],
    product_id: str,
) -> ParsedProduct | None:
    title = ''
    price: Decimal | None = None
    original_price: Decimal | None = None
    image_url: str | None = None
    in_stock = True
    rating: float | None = None
    review_count: int | None = None

    for value in iter_widget_dicts(payload):
        link = value.get('link') or value.get('action', {}).get('link')
        if isinstance(link, dict):
            link = link.get('link') or link.get('url')
        link_ok = isinstance(link, str) and product_id in link
        id_ok = str(value.get('skuId') or value.get('id') or '') == product_id
        if not link_ok and not id_ok:
            continue

        if not title:
            raw_title = value.get('title', '') or value.get('productTitle', '')
            if isinstance(raw_title, dict):
                raw_title = raw_title.get('text') or ''
            title = str(raw_title)

        tile_price, tile_original = _tile_prices(value)
        price = price or tile_price
        original_price = original_price or tile_original
        image_url = image_url or _tile_image(value)

        if value.get('isOutOfStock') is True:
            in_stock = False

        if rating is None:
            rating = _as_float(value.get('rating') or value.get('reviewsRating'))
        if review_count is None:
            review_count = _as_int(
                value.get('reviewsCount')
                or value.get('feedbacks')
                or value.get('reviews')
            )

    if price is None:
        return None

    return ParsedProduct(
        external_id=product_id,
        title=title or f'Ozon #{product_id}',
        price=price,
        original_price=original_price,
        discount_percent=(
            int((original_price - price) / original_price * 100)
            if original_price and original_price > 0 and price < original_price
            else None
        ),
        in_stock=in_stock,
        image_url=image_url,
        product_url=f'https://www.ozon.ru/product/{product_id}/',
        rating=rating,
        review_count=review_count,
    )


def parse_price_string(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if not isinstance(raw, str):
        return None
    cleaned = ''.join(ch for ch in raw if ch.isdigit() or ch in ',.')
    cleaned = cleaned.replace(',', '.')
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _as_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
