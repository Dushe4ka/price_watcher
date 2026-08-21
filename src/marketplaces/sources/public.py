from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote_plus

import httpx

from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    CategoryRequest,
    ProductRequest,
    SearchRequest,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
)
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode
from src.marketplaces.validation import ValidationState, validate_yandex_html
from src.parsers.base import ParsedProduct
from src.parsers.utils import (
    NotFoundError,
    ParsingError,
    create_http_client,
    get_random_ua,
)
from src.parsers.yandex_market import YandexMarketParser
from src.parsers.ym_api import build_product_url, iter_ld_json_products


HttpClientFactory = Callable[..., httpx.AsyncClient]


class _DisabledPublicSource:
    async def crawl_category(
        self,
        request: CategoryRequest,
    ) -> SourceResult[CategoryCrawlResult]:
        return _disabled_result()

    async def parse_product(
        self,
        request: ProductRequest,
    ) -> SourceResult[ParsedProduct]:
        return _disabled_result()

    async def search_products(
        self,
        request: SearchRequest,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        return _disabled_result()


class OzonPublicSource(_DisabledPublicSource):
    """Disabled until Ozon has a proven browser-independent transport."""


class WildberriesPublicSource(_DisabledPublicSource):
    """Disabled until WB has a proven browser-independent transport."""


class YandexPublicSource:
    """Yandex Market source over the browser-independent HTTP path."""

    def __init__(
        self,
        client_factory: HttpClientFactory = create_http_client,
    ) -> None:
        self._client_factory = client_factory
        self._parser = YandexMarketParser()

    async def crawl_category(
        self,
        request: CategoryRequest,
    ) -> SourceResult[CategoryCrawlResult]:
        # Task 12 owns trusted category-slug to URL resolution.
        return _disabled_result()

    async def parse_product(
        self,
        request: ProductRequest,
    ) -> SourceResult[ParsedProduct]:
        started = time.monotonic()
        try:
            html = await self._fetch_html(
                build_product_url(request.product_id),
                not_found_on_404=True,
            )
            state = validate_yandex_html(html)
            if state is ValidationState.VALID_EMPTY:
                return _not_found_result(started)
            _raise_for_validation_state(state)
            item = next(iter_ld_json_products(html), None)
            if item is None:
                raise MarketplaceSourceError(
                    SourceOutcome.PARSE_DRIFT,
                    SafeErrorCode.PARSE_DRIFT,
                )
            product = self._parser._extract_from_json_ld(
                item,
                request.product_id,
                html,
            )
        except MarketplaceSourceError as exc:
            return _failure_result(exc, started)
        except NotFoundError:
            return _not_found_result(started)
        except ParsingError as exc:
            error = MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
                cause=exc,
            )
            return _failure_result(error, started)
        return _success_result(product, started, item_count=1)

    async def search_products(
        self,
        request: SearchRequest,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        started = time.monotonic()
        url = (
            'https://market.yandex.ru/search?text='
            f'{quote_plus(request.query)}&page={request.page}'
        )
        try:
            html = await self._fetch_html(url)
            state = validate_yandex_html(html)
            if state is ValidationState.VALID_EMPTY:
                return _empty_result(started)
            _raise_for_validation_state(state)
            products = tuple(
                self._parser._extract_from_json_ld(
                    item,
                    _product_id(item),
                    html,
                )
                for item in list(iter_ld_json_products(html))[:request.limit]
            )
            if not products:
                raise MarketplaceSourceError(
                    SourceOutcome.PARSE_DRIFT,
                    SafeErrorCode.PARSE_DRIFT,
                )
        except MarketplaceSourceError as exc:
            return _failure_result(exc, started)
        except (ParsingError, ValueError) as exc:
            error = MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
                cause=exc,
            )
            return _failure_result(error, started)
        return _success_result(products, started, item_count=len(products))

    async def _fetch_html(
        self,
        url: str,
        *,
        not_found_on_404: bool = False,
    ) -> str:
        headers = {
            'User-Agent': get_random_ua(),
            'Accept-Language': 'ru-RU,ru;q=0.9',
        }
        try:
            async with self._client_factory(headers=headers) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
                cause=exc,
            ) from exc
        if response.status_code == 404 and not_found_on_404:
            raise NotFoundError('Yandex Market product not found')
        _raise_for_status(response.status_code)
        return response.text


def _product_id(item: dict[str, Any]) -> str:
    from src.parsers.ym_api import product_id_from_ld_json

    product_id = product_id_from_ld_json(item)
    if product_id is None:
        raise ValueError('product schema has no routing id')
    return product_id


def _raise_for_status(status_code: int) -> None:
    if status_code == 403:
        raise MarketplaceSourceError(
            SourceOutcome.CHALLENGE,
            SafeErrorCode.CHALLENGE_DETECTED,
        )
    if status_code == 429:
        raise MarketplaceSourceError(
            SourceOutcome.RATE_LIMITED,
            SafeErrorCode.RATE_LIMITED,
        )
    if status_code >= 500:
        raise MarketplaceSourceError(
            SourceOutcome.TRANSPORT_ERROR,
            SafeErrorCode.TRANSPORT_FAILED,
        )
    if status_code != 200:
        raise MarketplaceSourceError(
            SourceOutcome.PARSE_DRIFT,
            SafeErrorCode.PARSE_DRIFT,
        )


def _raise_for_validation_state(state: ValidationState) -> None:
    if state is ValidationState.CHALLENGE:
        raise MarketplaceSourceError(
            SourceOutcome.CHALLENGE,
            SafeErrorCode.CHALLENGE_DETECTED,
        )
    if state is ValidationState.DRIFT:
        raise MarketplaceSourceError(
            SourceOutcome.PARSE_DRIFT,
            SafeErrorCode.PARSE_DRIFT,
        )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _disabled_result() -> SourceResult[Any]:
    return SourceResult(
        source=SourceName.PUBLIC,
        outcome=SourceOutcome.DISABLED,
        value=None,
        attempt=SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=SourceOutcome.DISABLED,
            duration_ms=0,
            item_count=0,
        ),
    )


def _empty_result(started: float) -> SourceResult[Any]:
    return SourceResult(
        source=SourceName.PUBLIC,
        outcome=SourceOutcome.EMPTY,
        value=None,
        attempt=SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=SourceOutcome.EMPTY,
            duration_ms=_duration_ms(started),
            item_count=0,
        ),
    )


def _not_found_result(started: float) -> SourceResult[Any]:
    return SourceResult(
        source=SourceName.PUBLIC,
        outcome=SourceOutcome.NOT_FOUND,
        value=None,
        attempt=SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=SourceOutcome.NOT_FOUND,
            duration_ms=_duration_ms(started),
            item_count=0,
        ),
    )


def _success_result(
    value: Any,
    started: float,
    item_count: int,
) -> SourceResult[Any]:
    return SourceResult(
        source=SourceName.PUBLIC,
        outcome=SourceOutcome.SUCCESS,
        value=value,
        attempt=SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=SourceOutcome.SUCCESS,
            duration_ms=_duration_ms(started),
            item_count=item_count,
        ),
    )


def _failure_result(
    error: MarketplaceSourceError,
    started: float,
) -> SourceResult[Any]:
    return SourceResult(
        source=SourceName.PUBLIC,
        outcome=error.outcome,
        value=None,
        attempt=SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=error.outcome,
            duration_ms=_duration_ms(started),
            item_count=0,
            error_code=error.error_code,
        ),
    )
