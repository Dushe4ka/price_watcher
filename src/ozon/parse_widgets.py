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
    return result


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
    for raw_value in payload.get('widgetStates', {}).values():
        if len(result) >= limit:
            break
        text = raw_value if isinstance(raw_value, str) else json.dumps(raw_value)
        for match in OZON_PRODUCT_RE.finditer(text):
            if len(result) >= limit:
                break
            product_id = match.group(1)
            if product_id in result:
                continue
            candidate = _extract_single_product(payload, product_id)
            if candidate is not None:
                result[product_id] = candidate
    return result


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
        if isinstance(link, str) and product_id not in link:
            continue
        if not title:
            title = value.get('title', '') or value.get('productTitle', '')

        web_price = value.get('webPrice') or value.get('price')
        if isinstance(web_price, dict):
            price = price or parse_price_string(web_price.get('price'))
            original_price = original_price or parse_price_string(
                web_price.get('originalPrice')
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

        rating_raw = value.get('rating') or value.get('reviewsRating')
        if rating is None and rating_raw is not None:
            try:
                rating = float(rating_raw)
            except (ValueError, TypeError):
                pass

        reviews_raw = (
            value.get('reviewsCount')
            or value.get('feedbacks')
            or value.get('reviews')
        )
        if review_count is None and reviews_raw is not None:
            try:
                review_count = int(reviews_raw)
            except (ValueError, TypeError):
                pass

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
