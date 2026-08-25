"""Typed Apify fallback source using a project-owned synthetic dataset schema.

The field mapping below deliberately does not claim compatibility with any live
Apify actor. Runtime remains disabled until a token and an operation actor ID
are configured.
"""

from __future__ import annotations

import math
import time
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any, TypeVar

from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.apify_client import ApifyClient, _ApifyDisabledError
from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
)
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode
from src.parsers.base import BaseParser, ParsedProduct


T = TypeVar('T')


class ApifySource:
    """Map controlled Apify dataset items into existing source contracts."""

    def __init__(
        self,
        marketplace: MarketplaceName,
        client: ApifyClient,
    ) -> None:
        self._marketplace = marketplace
        self._client = client

    async def crawl_category(
        self,
        request: CategoryRequest,
    ) -> SourceResult[CategoryCrawlResult]:
        started = time.monotonic()
        dataset = await self._dataset_or_result(
            MarketplaceOperation.CRAWL_CATEGORY,
            request,
            started,
        )
        if isinstance(dataset, SourceResult):
            return dataset
        if not dataset:
            return _result(SourceOutcome.EMPTY, None, started)
        try:
            products = _map_products(self._marketplace, dataset, request.limit)
        except (DecimalException, OverflowError, TypeError, ValueError):
            return _failure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
                started,
            )
        result = CategoryCrawlResult(
            marketplace=self._marketplace,
            category_slug=request.category_slug,
            product_ids=[product.external_id for product in products],
            product_urls=[product.product_url or '' for product in products],
            pre_parsed={product.external_id: product for product in products},
        )
        return _result(SourceOutcome.SUCCESS, result, started,
                       item_count=len(products))

    async def parse_product(
        self,
        request: ProductRequest,
    ) -> SourceResult[ParsedProduct]:
        started = time.monotonic()
        dataset = await self._dataset_or_result(
            MarketplaceOperation.PARSE_PRODUCT,
            request,
            started,
        )
        if isinstance(dataset, SourceResult):
            return dataset
        if not dataset:
            return _result(SourceOutcome.EMPTY, None, started)
        try:
            product = _map_product(self._marketplace, dataset[0])
            if product.external_id != request.product_id:
                raise ValueError('synthetic product id mismatch')
        except (DecimalException, OverflowError, TypeError, ValueError):
            return _failure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
                started,
            )
        return _result(SourceOutcome.SUCCESS, product, started, item_count=1)

    async def search_products(
        self,
        request: SearchRequest,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        started = time.monotonic()
        dataset = await self._dataset_or_result(
            MarketplaceOperation.SEARCH_PRODUCTS,
            request,
            started,
        )
        if isinstance(dataset, SourceResult):
            return dataset
        if not dataset:
            return _result(SourceOutcome.EMPTY, None, started)
        try:
            products = _map_products(self._marketplace, dataset, request.limit)
        except (DecimalException, OverflowError, TypeError, ValueError):
            return _failure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
                started,
            )
        return _result(SourceOutcome.SUCCESS, products, started,
                       item_count=len(products))

    async def _dataset_or_result(
        self,
        operation: MarketplaceOperation,
        request: CategoryRequest | ProductRequest | SearchRequest,
        started: float,
    ) -> list[dict[str, object]] | SourceResult[Any]:
        if not self._client.is_enabled(self._marketplace, operation):
            return _result(SourceOutcome.DISABLED, None, started)
        try:
            return await self._client.run_actor(
                self._marketplace,
                operation,
                request,
            )
        except _ApifyDisabledError:
            return _result(SourceOutcome.DISABLED, None, started)
        except MarketplaceSourceError as exc:
            return _failure(
                exc.outcome,
                exc.error_code,
                started,
                retry_after_ms=exc.retry_after_ms,
            )
        except ValueError:
            return _failure(
                SourceOutcome.INVALID_CONFIG,
                SafeErrorCode.INVALID_CONFIG,
                started,
            )


def _map_products(
    marketplace: MarketplaceName,
    dataset: list[dict[str, object]],
    limit: int,
) -> tuple[ParsedProduct, ...]:
    products = tuple(
        _map_product(marketplace, item) for item in dataset[:limit]
    )
    if not products:
        raise ValueError('empty synthetic product dataset')
    return products


def _map_product(
    marketplace: MarketplaceName,
    item: dict[str, object],
) -> ParsedProduct:
    """Map one synthetic provider item; it is not a live actor schema."""
    product_id = _product_id(item.get('id'))
    title = _required_text(item.get('title'))
    price = _price(item.get('price'))
    original_price = _optional_price(item.get('originalPrice'))
    if original_price is not None and original_price <= price:
        original_price = None
    in_stock = _optional_bool(item.get('inStock'), default=True)
    image_url = _optional_text(item.get('imageUrl'))
    rating = _optional_float(item.get('rating'))
    review_count = _optional_nonnegative_int(item.get('reviewCount'))
    return ParsedProduct(
        external_id=product_id,
        title=title,
        price=price,
        original_price=original_price,
        discount_percent=BaseParser.calc_discount(price, original_price),
        in_stock=in_stock,
        image_url=image_url,
        product_url=_product_url(marketplace, product_id),
        rating=rating,
        review_count=review_count,
    )


def _product_url(marketplace: MarketplaceName, product_id: str) -> str:
    if marketplace == 'wildberries':
        return f'https://www.wildberries.ru/catalog/{product_id}/detail.aspx'
    if marketplace == 'ozon':
        return f'https://www.ozon.ru/product/{product_id}/'
    return f'https://market.yandex.ru/card/x/{product_id}'


def _product_id(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError('invalid synthetic product id')
    product_id = str(value) if isinstance(value, (int, str)) else ''
    if not product_id.isdigit() or product_id.startswith('0'):
        raise ValueError('invalid synthetic product id')
    return product_id


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('invalid synthetic text field')
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value)


def _price(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid synthetic price')
    try:
        price = Decimal(str(value))
    except (InvalidOperation, OverflowError, TypeError, ValueError) as exc:
        raise ValueError('invalid synthetic price') from exc
    if not price.is_finite() or price <= 0:
        raise ValueError('invalid synthetic price')
    return price


def _optional_price(value: object) -> Decimal | None:
    return None if value is None else _price(value)


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError('invalid synthetic boolean')
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('invalid synthetic number')
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError('invalid synthetic number') from exc
    if not math.isfinite(number):
        raise ValueError('invalid synthetic number')
    return number


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('invalid synthetic count')
    return value


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _result(
    outcome: SourceOutcome,
    value: T | None,
    started: float,
    *,
    item_count: int = 0,
) -> SourceResult[T]:
    return SourceResult(
        source=SourceName.APIFY,
        outcome=outcome,
        value=value,
        attempt=SourceAttempt(
            source=SourceName.APIFY,
            outcome=outcome,
            duration_ms=_duration_ms(started),
            item_count=item_count,
        ),
    )


def _failure(
    outcome: SourceOutcome,
    error_code: SafeErrorCode,
    started: float,
    *,
    retry_after_ms: int | None = None,
) -> SourceResult[Any]:
    return SourceResult(
        source=SourceName.APIFY,
        outcome=outcome,
        value=None,
        attempt=SourceAttempt(
            source=SourceName.APIFY,
            outcome=outcome,
            duration_ms=_duration_ms(started),
            item_count=0,
            error_code=error_code,
            retry_after_ms=retry_after_ms,
        ),
    )
