"""Shared stubs for composition-root tests without browser or network use."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from src.core.config import Settings
from src.core.config import settings as default_settings
from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    MarketplaceName,
    SourceName,
    SourceOutcome,
    SourceResult,
    source_empty,
    source_failure,
    source_success,
)
from src.marketplaces.errors import SafeErrorCode
from src.parsers.base import ParsedProduct


def parsed_product(
    external_id: str = '9000001',
    *,
    title: str = 'Synthetic Item',
    price: str = '1000',
    in_stock: bool = True,
) -> ParsedProduct:
    """Build one synthetic product; no live marketplace data is involved."""
    return ParsedProduct(
        external_id=external_id,
        title=title,
        price=Decimal(price),
        in_stock=in_stock,
        product_url=f'https://www.ozon.ru/product/{external_id}/',
    )


def crawl_result(
    marketplace: MarketplaceName = 'ozon',
    *,
    category_slug: str = 'beauty',
    product_ids: Sequence[str] = ('9000001',),
) -> CategoryCrawlResult:
    """Build one synthetic category crawl payload."""
    products = {
        product_id: parsed_product(product_id)
        for product_id in product_ids
    }
    return CategoryCrawlResult(
        marketplace=marketplace,
        category_slug=category_slug,
        product_ids=list(product_ids),
        product_urls=[
            products[product_id].product_url or ''
            for product_id in product_ids
        ],
        pre_parsed=products,
    )


def success(source: SourceName, value: Any) -> SourceResult[Any]:
    """Return one successful source result."""
    return source_success(source, value)


def empty(source: SourceName) -> SourceResult[Any]:
    """Return one structurally validated empty source result."""
    return source_empty(source)


def challenge(source: SourceName) -> SourceResult[Any]:
    """Return one challenge failure result."""
    return source_failure(
        source,
        SourceOutcome.CHALLENGE,
        SafeErrorCode.CHALLENGE_DETECTED,
    )


def transport_error(source: SourceName) -> SourceResult[Any]:
    """Return one retriable transport failure result."""
    return source_failure(
        source,
        SourceOutcome.TRANSPORT_ERROR,
        SafeErrorCode.TRANSPORT_FAILED,
    )


def rate_limited(source: SourceName) -> SourceResult[Any]:
    """Return one retriable rate limited failure result."""
    return source_failure(
        source,
        SourceOutcome.RATE_LIMITED,
        SafeErrorCode.RATE_LIMITED,
    )


def retry_settings(
    *,
    max_attempts: int = 2,
    base_delay_ms: int = 250,
    max_delay_ms: int = 1000,
    total_timeout_sec: int = 30,
    operation_timeout_sec: int = 90,
) -> Settings:
    """Return real settings with only the retry knobs overridden.

    ``total_timeout_sec`` is the per-source budget
    (``marketplace_total_timeout_sec``); ``operation_timeout_sec`` is the
    budget shared across the whole fallback chain
    (``marketplace_operation_timeout_sec``). They are deliberately kept as
    two independent parameters so a test can exhaust one without touching
    the other.
    """
    return default_settings.model_copy(
        update={
            'marketplace_retry_max_attempts': max_attempts,
            'marketplace_retry_base_delay_ms': base_delay_ms,
            'marketplace_retry_max_delay_ms': max_delay_ms,
            'marketplace_total_timeout_sec': total_timeout_sec,
            'marketplace_operation_timeout_sec': operation_timeout_sec,
        },
    )


class RecordingSleep:
    """Async sleep replacement recording every requested delay."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class FakeClock:
    """Monotonic-shaped clock advancing a fixed step per reading."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self._now = start
        self._step = step
        self.readings = 0

    def __call__(self) -> float:
        self.readings += 1
        now = self._now
        self._now += self._step
        return now

    def advance(self, delta: float) -> None:
        """Move the clock forward without consuming a reading."""
        self._now += delta


class StubSource:
    """Source adapter returning scripted results per marketplace operation."""

    def __init__(
        self,
        source: SourceName,
        *results: SourceResult[Any],
        crawl: Sequence[SourceResult[Any]] = (),
        product: Sequence[SourceResult[Any]] = (),
        search: Sequence[SourceResult[Any]] = (),
    ) -> None:
        self.source = source
        self.requests: list[Any] = []
        self._default = list(results)
        self._scripts = {
            'crawl_category': list(crawl),
            'parse_product': list(product),
            'search_products': list(search),
        }

    async def crawl_category(self, request: Any) -> SourceResult[Any]:
        return self._next('crawl_category', request)

    async def parse_product(self, request: Any) -> SourceResult[Any]:
        return self._next('parse_product', request)

    async def search_products(self, request: Any) -> SourceResult[Any]:
        return self._next('search_products', request)

    def _next(self, operation: str, request: Any) -> SourceResult[Any]:
        self.requests.append(request)
        results = self._scripts[operation] or self._default
        if not results:
            raise AssertionError('stub source ran out of scripted results')
        if len(results) == 1:
            return results[0]
        return results.pop(0)


class TimeConsumingSource:
    """Source stub that advances the shared clock before returning.

    Models a source that spends its own entire per-invocation timeout
    before failing (e.g. a browser navigation that runs to its own
    deadline), so a test can prove the shared operation deadline still
    leaves room for the sources after it in the chain.
    """

    def __init__(
        self,
        source: SourceName,
        clock: Any,
        elapsed_sec: float,
        result: SourceResult[Any],
    ) -> None:
        self.source = source
        self.requests: list[Any] = []
        self._clock = clock
        self._elapsed_sec = elapsed_sec
        self._result = result

    async def crawl_category(self, request: Any) -> SourceResult[Any]:
        return self._consume(request)

    async def parse_product(self, request: Any) -> SourceResult[Any]:
        return self._consume(request)

    async def search_products(self, request: Any) -> SourceResult[Any]:
        return self._consume(request)

    def _consume(self, request: Any) -> SourceResult[Any]:
        self.requests.append(request)
        self._clock.advance(self._elapsed_sec)
        return self._result


class StubRegistry:
    """Registry stub exposing a fixed chain and a counted close.

    The chain entries are only duck typed as source adapters, so a real
    production source can stand in one slot while stubs fill the others.
    """

    def __init__(
        self,
        chain: Sequence[tuple[SourceName, Any]],
        *,
        start_error: Exception | None = None,
    ) -> None:
        self._chain = tuple(chain)
        self._start_error = start_error
        self.close_calls = 0
        self.start_calls = 0
        self.refresh_calls = 0
        self.closed = False

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError('registry is closed')
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error

    def refresh_category_urls(self) -> None:
        if self.closed:
            raise RuntimeError('registry is closed')
        self.refresh_calls += 1

    def sources_for(
        self,
        marketplace: MarketplaceName,
    ) -> tuple[tuple[SourceName, Any], ...]:
        del marketplace
        if self.closed:
            raise RuntimeError('registry is closed')
        return self._chain

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_calls += 1
