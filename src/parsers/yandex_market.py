from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.utils import (
    BlockedError,
    NotFoundError,
    ParsingError,
    create_http_client,
    get_random_ua,
    retry_request,
)
from src.parsers.ym_api import (
    PRODUCT_URL_RE,
    build_product_url,
    extract_offer_prices,
    iter_ld_json_products,
    product_id_from_ld_json,
)


class YandexMarketParser(BaseParser):
    marketplace = 'yandex_market'

    def extract_product_id(self, url: str) -> str:
        match = PRODUCT_URL_RE.search(url)
        if not match:
            raise ValueError(
                f'Cannot extract Yandex Market product ID from URL: {url}'
            )
        return match.group(1)

    def build_url(self, product_id: str) -> str:
        return build_product_url(product_id)

    @retry_request
    async def parse_product(self, url_or_id: str) -> ParsedProduct:
        is_url = url_or_id.startswith('http') or 'market.yandex.ru' in url_or_id
        if is_url:
            page_url = url_or_id if url_or_id.startswith('http') else f'https://{url_or_id}'
            try:
                url_hint = self.extract_product_id(url_or_id)
            except ValueError:
                url_hint = None
        else:
            url_hint = url_or_id.strip()
            page_url = self.build_url(url_hint)

        headers = {
            'User-Agent': get_random_ua(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        async with create_http_client(headers=headers) as client:
            response = await client.get(page_url)
            if response.status_code == 403:
                raise BlockedError(
                    f'Yandex Market blocked request for {url_hint}'
                )
            if response.status_code == 404:
                raise NotFoundError(
                    f'Yandex Market product {url_hint} not found'
                )
            response.raise_for_status()
            html = response.text

        for item in iter_ld_json_products(html):
            product_id = product_id_from_ld_json(item) or url_hint
            if product_id is None:
                continue
            return self._extract_from_json_ld(item, product_id, html)

        raise NotFoundError(
            f'Yandex Market product {url_hint} not found (no Product data)'
        )

    def _extract_from_json_ld(
        self,
        item: dict[str, Any],
        product_id: str,
        html: str,
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
        in_stock = True
        for offer in offers_list:
            price = price or _price_from_offer(offer.get('price'))
            availability = offer.get('availability', '')
            if availability and 'InStock' not in availability:
                in_stock = False

        patch_price, original_price = extract_offer_prices(html, product_id)
        price = patch_price or price
        if price is None:
            raise ParsingError(
                f'No price in Yandex Market data for {product_id}'
            )
        if original_price is not None and original_price <= price:
            original_price = None

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


def _price_from_offer(raw: Any) -> Decimal | None:
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
    except Exception:
        return None
