"""Pure (no-network) parsing of the raw dicts produced by the JS snippets in
``src/wb/constants.py`` — kept separate from the browser session so it's unit
testable without Playwright."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from src.parsers.base import BaseParser, ParsedProduct
from src.wb.constants import build_product_url

_PRICE_CLEAN_RE = re.compile(r'[^\d]')
_REVIEW_COUNT_RE = re.compile(r'[\d\xa0 ]+')
_TITLE_SUFFIX_RE = re.compile(r'\s+купить\s')


def parse_price(text: str | None) -> Decimal | None:
    if not text:
        return None
    cleaned = _PRICE_CLEAN_RE.sub('', text)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_rating(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text.strip().replace(',', '.'))
    except ValueError:
        return None


def parse_review_count(text: str | None) -> int | None:
    if not text:
        return None
    match = _REVIEW_COUNT_RE.search(text)
    if not match:
        return None
    digits = match.group(0).replace('\xa0', '').replace(' ', '')
    return int(digits) if digits.isdigit() else None


def extract_title_from_page_title(page_title: str, product_id: str) -> str:
    """WB detail-page <title> follows the pattern
    "<name> <nmId> купить за <price> ₽ в интернет‑магазине Wildberries" — cut
    off the templated suffix rather than relying on a (nonexistent) <h1>."""
    marker = f' {product_id} '
    idx = page_title.find(marker)
    if idx != -1:
        return page_title[:idx].strip()
    match = _TITLE_SUFFIX_RE.search(page_title)
    if match:
        return page_title[:match.start()].strip()
    return page_title.strip()


def card_to_parsed_product(raw: dict[str, Any]) -> ParsedProduct | None:
    product_id = raw.get('nmId')
    if not product_id:
        return None
    price = parse_price(raw.get('priceCurrent'))
    if price is None:
        return None
    original_price = parse_price(raw.get('priceOld'))

    title = raw.get('title') or f'Wildberries #{product_id}'
    brand = raw.get('brand')
    if brand and not title.lower().startswith(brand.lower()):
        title = f'{brand} {title}'

    return ParsedProduct(
        external_id=str(product_id),
        title=title,
        price=price,
        original_price=original_price,
        discount_percent=BaseParser.calc_discount(price, original_price),
        in_stock=True,
        image_url=raw.get('imageUrl'),
        product_url=build_product_url(str(product_id)),
        rating=parse_rating(raw.get('ratingValue')),
        review_count=parse_review_count(raw.get('reviewText')),
    )


def detail_to_parsed_product(
    raw: dict[str, Any],
    product_id: str,
) -> ParsedProduct | None:
    price = parse_price(raw.get('priceCurrent'))
    if price is None:
        return None
    original_price = parse_price(raw.get('priceOld'))
    page_title = raw.get('pageTitle') or ''
    title = (
        extract_title_from_page_title(page_title, product_id)
        if page_title
        else f'Wildberries #{product_id}'
    )

    return ParsedProduct(
        external_id=product_id,
        title=title or f'Wildberries #{product_id}',
        price=price,
        original_price=original_price,
        discount_percent=BaseParser.calc_discount(price, original_price),
        in_stock=True,
        image_url=raw.get('imageUrl'),
        product_url=build_product_url(product_id),
        rating=parse_rating(raw.get('ratingValue')),
        review_count=parse_review_count(raw.get('reviewText')),
    )
