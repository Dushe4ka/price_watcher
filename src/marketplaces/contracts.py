from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from src.marketplaces.errors import SafeErrorCode


MarketplaceName = Literal['wildberries', 'ozon', 'yandex_market']

T = TypeVar('T')


class SourceName(StrEnum):
    PUBLIC = 'public'
    BROWSER = 'browser'
    APIFY = 'apify'


class MarketplaceOperation(StrEnum):
    CRAWL_CATEGORY = 'crawl_category'
    PARSE_PRODUCT = 'parse_product'
    SEARCH_PRODUCTS = 'search_products'


class SourceOutcome(StrEnum):
    SUCCESS = 'success'
    EMPTY = 'empty'
    NOT_FOUND = 'not_found'
    CHALLENGE = 'challenge'
    RATE_LIMITED = 'rate_limited'
    TRANSPORT_ERROR = 'transport_error'
    PARSE_DRIFT = 'parse_drift'
    AUTH_ERROR = 'auth_error'
    INVALID_CONFIG = 'invalid_config'
    DISABLED = 'disabled'


@dataclass(frozen=True, slots=True)
class CategoryRequest:
    category_slug: str
    limit: int


@dataclass(frozen=True, slots=True)
class ProductRequest:
    product_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str = field(repr=False)
    limit: int
    page: int = 1


@dataclass(frozen=True, slots=True)
class SourceAttempt:
    source: SourceName
    outcome: SourceOutcome
    duration_ms: int
    item_count: int
    error_code: SafeErrorCode | None = None
    transport_attempts: int = 1
    retry_after_ms: int | None = None
    """Server-advertised cooldown, present only for a rate limited attempt.

    ``None`` means the source published no usable hint: an in-page ``429``
    exposes no response headers to the browser transport, so a browser
    attempt legitimately carries no value rather than a fabricated one.
    """

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError('duration_ms must not be negative')
        if self.item_count < 0:
            raise ValueError('item_count must not be negative')
        if self.transport_attempts < 0:
            raise ValueError('transport_attempts must not be negative')
        if self.retry_after_ms is not None:
            if self.retry_after_ms < 0:
                raise ValueError('retry_after_ms must not be negative')
            if self.outcome is not SourceOutcome.RATE_LIMITED:
                raise ValueError(
                    'retry_after_ms requires a rate limited outcome'
                )
        if self.transport_attempts == 0 and (
            self.outcome is not SourceOutcome.TRANSPORT_ERROR
            or self.error_code is not SafeErrorCode.TIMEOUT
        ):
            raise ValueError(
                'zero transport_attempts requires a timeout transport error'
            )


@dataclass(frozen=True, slots=True)
class SourceResult(Generic[T]):
    source: SourceName
    outcome: SourceOutcome
    value: T | None = field(repr=False)
    attempt: SourceAttempt

    def __post_init__(self) -> None:
        if self.attempt.source is not self.source:
            raise ValueError('attempt source must match result source')
        if self.attempt.outcome is not self.outcome:
            raise ValueError('attempt outcome must match result outcome')
        if self.outcome is SourceOutcome.SUCCESS and self.value is None:
            raise ValueError('success requires a value')
        if (
            self.outcome is not SourceOutcome.SUCCESS
            and self.value is not None
        ):
            raise ValueError('failure cannot carry a value')


@dataclass(frozen=True, slots=True)
class MarketplaceResult(Generic[T]):
    marketplace: MarketplaceName
    operation: MarketplaceOperation
    outcome: SourceOutcome
    value: T | None = field(repr=False)
    attempts: tuple[SourceAttempt, ...]
    selected_source: SourceName | None

    def __post_init__(self) -> None:
        if self.outcome is SourceOutcome.SUCCESS and self.value is None:
            raise ValueError('success requires a value')
        if (
            self.outcome is not SourceOutcome.SUCCESS
            and self.value is not None
        ):
            raise ValueError('failure cannot carry a value')


def source_success(source: SourceName, value: T) -> SourceResult[T]:
    """Create a successful result with a synthetic zero-duration attempt."""
    return SourceResult(
        source=source,
        outcome=SourceOutcome.SUCCESS,
        value=value,
        attempt=SourceAttempt(
            source=source,
            outcome=SourceOutcome.SUCCESS,
            duration_ms=0,
            item_count=0,
        ),
    )


def source_empty(source: SourceName) -> SourceResult[None]:
    """Create an empty result after source-specific structural validation."""
    return SourceResult(
        source=source,
        outcome=SourceOutcome.EMPTY,
        value=None,
        attempt=SourceAttempt(
            source=source,
            outcome=SourceOutcome.EMPTY,
            duration_ms=0,
            item_count=0,
        ),
    )


def source_failure(
    source: SourceName,
    outcome: SourceOutcome,
    error_code: SafeErrorCode,
    retry_after_ms: int | None = None,
) -> SourceResult[None]:
    """Create a failure result with safe diagnostic metadata only."""
    if outcome in (SourceOutcome.SUCCESS, SourceOutcome.EMPTY):
        raise ValueError('failure factory requires a failure outcome')
    return SourceResult(
        source=source,
        outcome=outcome,
        value=None,
        attempt=SourceAttempt(
            source=source,
            outcome=outcome,
            duration_ms=0,
            item_count=0,
            error_code=error_code,
            retry_after_ms=retry_after_ms,
        ),
    )
