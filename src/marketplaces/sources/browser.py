"""Bounded same-page browser fallback adapters for all marketplaces."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, TypeVar
from urllib.parse import quote, urlsplit

from src.browser.allowlist import (
    UnsafeMarketplaceUrl,
    build_marketplace_url,
    validate_main_frame_url,
)
from src.browser.contracts import PageLike
from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.models import ChallengeResolution
from src.core.config import settings
from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    ProductRequest,
    SearchRequest,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.retry import OperationDeadline
from src.marketplaces.validation import (
    ValidationState,
    validate_ozon_payload,
    validate_wb_dom_snapshot,
    validate_yandex_html,
)
from src.ozon.constants import OZON_HOME_URL, OZON_MOBILE_HEADERS
from src.ozon.parse_widgets import extract_product_summary_map
from src.parsers.base import ParsedProduct
from src.parsers.yandex_market import YandexMarketParser
from src.parsers.ym_api import iter_ld_json_products, product_id_from_ld_json
from src.wb.constants import CATEGORY_CARDS_JS, DETAIL_PAGE_JS
from src.wb.dom_extract import card_to_parsed_product, detail_to_parsed_product


T = TypeVar('T')
Clock = Callable[[], float]

_MAX_ITEMS = 100
_MAX_PAGE = 100
_MAX_QUERY_LENGTH = 500

# Evaluate results are JSON-derived values; bound traversal depth and charge a
# small fixed cost per non-string scalar so the size probe stays finite.
_MAX_RESULT_DEPTH = 32
_SCALAR_RESULT_BYTES = 32

# The Ozon capture envelope wraps an already byte-capped body in a short
# ``kind``/``status``/``url`` triple. Allow for that envelope without
# loosening the body cap enforced in the page and in ``_decode_capture``.
_CAPTURE_ENVELOPE_BYTES = 1024

# Fetch forbidden request headers: a browser silently drops these from the
# headers object handed to ``fetch()``, so passing one would advertise a value
# the request never actually carries. ``User-Agent`` is the load-bearing case
# here - the in-page fetch always goes out with the browser context's own UA,
# and the mobile UA in OZON_MOBILE_HEADERS is only honoured by the plain HTTP
# client in ``src/ozon/client.py``. See
# https://fetch.spec.whatwg.org/#forbidden-request-header
_FETCH_FORBIDDEN_HEADERS = frozenset((
    'accept-charset',
    'accept-encoding',
    'access-control-request-headers',
    'access-control-request-method',
    'connection',
    'content-length',
    'cookie',
    'cookie2',
    'date',
    'dnt',
    'expect',
    'host',
    'keep-alive',
    'origin',
    'permissions-policy',
    'referer',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
    'user-agent',
    'via',
))


class BrowserManagerLike(Protocol):
    """Lease surface required from the browser session manager."""

    def lease(
        self,
        marketplace: MarketplaceName,
    ) -> AbstractAsyncContextManager[PageLike]:
        """Lease one exact Page for a complete source operation."""


class _SourceFailure(Exception):
    __slots__ = ('error_code', 'outcome')

    def __init__(
        self,
        outcome: SourceOutcome,
        error_code: SafeErrorCode | None,
    ) -> None:
        self.outcome = outcome
        self.error_code = error_code
        super().__init__('browser source returned a typed outcome')


class _OperationState:
    __slots__ = ('expired', 'page')

    def __init__(self) -> None:
        self.expired = False
        self.page: PageLike | None = None


class _BrowserSourceBase:
    marketplace: MarketplaceName

    def __init__(
        self,
        manager: BrowserManagerLike,
        coordinator: ChallengeCoordinator,
        *,
        category_urls: Mapping[str, str] | None = None,
        total_timeout_sec: float | None = None,
        max_content_bytes: int | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        timeout = (
            float(settings.marketplace_total_timeout_sec)
            if total_timeout_sec is None
            else total_timeout_sec
        )
        content_limit = (
            settings.marketplace_max_content_bytes
            if max_content_bytes is None
            else max_content_bytes
        )
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError('total_timeout_sec must be finite and bounded')
        if not 0 < content_limit <= 10_485_760:
            raise ValueError('max_content_bytes must be bounded')
        self._manager = manager
        self._coordinator = coordinator
        self._category_urls = dict(category_urls or {})
        self._timeout_ms = max(1, math.ceil(timeout * 1000))
        self._max_content_bytes = content_limit
        self._clock = clock
        self._background_tasks: set[asyncio.Future[Any]] = set()

    async def crawl_category(
        self,
        request: CategoryRequest,
    ) -> SourceResult[CategoryCrawlResult]:
        started = self._clock()
        deadline = self._deadline()
        state = _OperationState()
        try:
            if not isinstance(request, CategoryRequest):
                raise _invalid_config()
            _validate_limit(request.limit)
            url = self._category_url(request.category_slug)
            return await self._complete_operation(
                deadline,
                state,
                lambda: self._execute_category(
                    request,
                    url,
                    started,
                    deadline,
                    state,
                ),
            )
        except _SourceFailure as exc:
            return self._failure(exc, started)
        except UnsafeMarketplaceUrl:
            return self._failure(
                _SourceFailure(
                    SourceOutcome.INVALID_CONFIG,
                    SafeErrorCode.INVALID_CONFIG,
                ),
                started,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failure(
                _SourceFailure(
                    SourceOutcome.TRANSPORT_ERROR,
                    SafeErrorCode.TRANSPORT_FAILED,
                ),
                started,
            )

    async def parse_product(
        self,
        request: ProductRequest,
    ) -> SourceResult[ParsedProduct]:
        started = self._clock()
        deadline = self._deadline()
        state = _OperationState()
        try:
            if not isinstance(request, ProductRequest):
                raise _invalid_config()
            _validate_product_id(request.product_id)
            url = build_marketplace_url(self.marketplace, request)
            return await self._complete_operation(
                deadline,
                state,
                lambda: self._execute_product(
                    request,
                    url,
                    started,
                    deadline,
                    state,
                ),
            )
        except _SourceFailure as exc:
            return self._failure(exc, started)
        except UnsafeMarketplaceUrl:
            return self._failure(
                _SourceFailure(
                    SourceOutcome.INVALID_CONFIG,
                    SafeErrorCode.INVALID_CONFIG,
                ),
                started,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failure(
                _SourceFailure(
                    SourceOutcome.TRANSPORT_ERROR,
                    SafeErrorCode.TRANSPORT_FAILED,
                ),
                started,
            )

    async def search_products(
        self,
        request: SearchRequest,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        started = self._clock()
        deadline = self._deadline()
        state = _OperationState()
        try:
            if not isinstance(request, SearchRequest):
                raise _invalid_config()
            _validate_search_request(request)
            url = build_marketplace_url(self.marketplace, request)
            return await self._complete_operation(
                deadline,
                state,
                lambda: self._execute_search(
                    request,
                    url,
                    started,
                    deadline,
                    state,
                ),
            )
        except _SourceFailure as exc:
            return self._failure(exc, started)
        except UnsafeMarketplaceUrl:
            return self._failure(
                _SourceFailure(
                    SourceOutcome.INVALID_CONFIG,
                    SafeErrorCode.INVALID_CONFIG,
                ),
                started,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failure(
                _SourceFailure(
                    SourceOutcome.TRANSPORT_ERROR,
                    SafeErrorCode.TRANSPORT_FAILED,
                ),
                started,
            )

    def _category_url(self, category_slug: str) -> str:
        if (
            not isinstance(category_slug, str)
            or not category_slug
            or len(category_slug) > 128
        ):
            raise _invalid_config()
        try:
            url = self._category_urls[category_slug]
        except KeyError:
            raise _invalid_config() from None
        return validate_main_frame_url(self.marketplace, url)

    def _deadline(self) -> OperationDeadline:
        return OperationDeadline.from_timeout_ms(
            self._timeout_ms,
            self._clock,
        )

    def _remaining_ms(self, deadline: OperationDeadline) -> float:
        remaining = deadline.expires_at - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise _timeout_failure()
        return remaining * 1000

    async def _complete_operation(
        self,
        deadline: OperationDeadline,
        state: _OperationState,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        remaining = deadline.expires_at - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            state.expired = True
            raise _timeout_failure()
        task = asyncio.ensure_future(operation())
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
        except BaseException:
            state.expired = True
            self._schedule_close(state.page)
            task.cancel()
            self._track_background_task(task)
            raise
        if task in done:
            if self._clock() > deadline.expires_at:
                state.expired = True
                self._schedule_close(state.page)
                _discard_completed_task(task)
                raise _timeout_failure()
            try:
                return task.result()
            except asyncio.TimeoutError:
                raise _timeout_failure() from None
        state.expired = True
        self._schedule_close(state.page)
        task.cancel()
        self._track_background_task(task)
        raise _timeout_failure()

    async def _navigate(
        self,
        page: PageLike,
        url: str,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> int:
        self._ensure_active(state, deadline)
        self._ensure_open(page)
        validate_main_frame_url(self.marketplace, url)
        response = await self._bounded(
            page,
            deadline,
            state,
            lambda: page.goto(
                url,
                wait_until='domcontentloaded',
                timeout=self._remaining_ms(deadline),
            ),
        )
        self._ensure_open(page)
        validate_main_frame_url(self.marketplace, page.url)
        if response is None:
            raise _SourceFailure(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )
        status = response.status
        if isinstance(status, bool) or not isinstance(status, int):
            raise _parse_drift()
        return status

    async def _resolve_challenge(
        self,
        page: PageLike,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> ChallengeResolution:
        self._ensure_safe_page(page, deadline, state)
        resolution = await self._bounded(
            page,
            deadline,
            state,
            lambda: self._coordinator.resolve(page, deadline=deadline),
        )
        self._ensure_safe_page(page, deadline, state)
        if resolution is ChallengeResolution.CHALLENGE_UNSOLVABLE:
            if self._clock() >= deadline.expires_at:
                raise _timeout_failure()
            raise _SourceFailure(
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_UNSUPPORTED,
            )
        if resolution not in (
            ChallengeResolution.NO_CHALLENGE,
            ChallengeResolution.SOLVED,
        ):
            raise _SourceFailure(
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_UNSUPPORTED,
            )
        return resolution

    async def _html(
        self,
        page: PageLike,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> str:
        self._ensure_safe_page(page, deadline, state)
        html = await self._bounded(
            page,
            deadline,
            state,
            page.content,
        )
        self._ensure_safe_page(page, deadline, state)
        if not isinstance(html, str):
            raise _parse_drift()
        try:
            encoded = html.encode('utf-8')
        except UnicodeError:
            raise _parse_drift() from None
        if len(encoded) > self._max_content_bytes:
            raise _SourceFailure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.CONTENT_TOO_LARGE,
            )
        return html

    async def _evaluate(
        self,
        page: PageLike,
        deadline: OperationDeadline,
        state: _OperationState,
        expression: str,
        *,
        max_bytes: int | None = None,
        open_page_error: _SourceFailure | None = None,
    ) -> object:
        self._ensure_safe_page(page, deadline, state)
        try:
            result = await self._bounded(
                page,
                deadline,
                state,
                lambda: page.evaluate(expression),
            )
        except (_SourceFailure, asyncio.CancelledError):
            raise
        except Exception:
            if page.is_closed() or open_page_error is None:
                raise
            raise open_page_error from None
        self._ensure_safe_page(page, deadline, state)
        _ensure_result_within_limit(
            result,
            self._max_content_bytes if max_bytes is None else max_bytes,
        )
        return result

    async def _bounded(
        self,
        page: PageLike,
        deadline: OperationDeadline,
        state: _OperationState,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        self._ensure_active(state, deadline)
        remaining = deadline.expires_at - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            state.expired = True
            self._schedule_close(page)
            raise _timeout_failure()
        task = asyncio.ensure_future(operation())
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
        except BaseException:
            state.expired = True
            self._schedule_close(page)
            task.cancel()
            self._track_background_task(task)
            raise
        if task in done:
            try:
                return task.result()
            except asyncio.TimeoutError:
                state.expired = True
                self._schedule_close(page)
                raise _timeout_failure() from None
        state.expired = True
        self._schedule_close(page)
        task.cancel()
        self._track_background_task(task)
        raise _timeout_failure()

    def _ensure_active(
        self,
        state: _OperationState,
        deadline: OperationDeadline,
    ) -> None:
        if state.expired or self._clock() >= deadline.expires_at:
            state.expired = True
            raise _timeout_failure()

    def _ensure_safe_page(
        self,
        page: PageLike,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> None:
        self._ensure_active(state, deadline)
        self._ensure_open(page)
        validate_main_frame_url(self.marketplace, page.url)

    def _schedule_close(self, page: PageLike | None) -> None:
        if page is None:
            return
        try:
            close_task = asyncio.ensure_future(_close_safely(page))
        except Exception:
            return
        self._track_background_task(close_task)

    def _track_background_task(
        self,
        task: asyncio.Future[Any],
    ) -> None:
        if task in self._background_tasks:
            return
        self._background_tasks.add(task)
        task.add_done_callback(self._consume_background_task)

    def _consume_background_task(
        self,
        task: asyncio.Future[Any],
    ) -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except BaseException:
            pass

    @staticmethod
    def _ensure_open(page: PageLike) -> None:
        if page.is_closed():
            raise _SourceFailure(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

    def _success(
        self,
        value: T,
        started: float,
        *,
        item_count: int,
    ) -> SourceResult[T]:
        return _result(
            SourceOutcome.SUCCESS,
            value,
            started,
            self._clock,
            item_count=item_count,
        )

    def _semantic_empty(
        self,
        started: float,
        *,
        product: bool = False,
    ) -> SourceResult[Any]:
        outcome = SourceOutcome.NOT_FOUND if product else SourceOutcome.EMPTY
        return _result(outcome, None, started, self._clock)

    def _failure(
        self,
        failure: _SourceFailure,
        started: float,
    ) -> SourceResult[Any]:
        return _result(
            failure.outcome,
            None,
            started,
            self._clock,
            error_code=failure.error_code,
        )

    async def _execute_category(
        self,
        request: CategoryRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[CategoryCrawlResult]:
        raise NotImplementedError

    async def _execute_product(
        self,
        request: ProductRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[ParsedProduct]:
        raise NotImplementedError

    async def _execute_search(
        self,
        request: SearchRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        raise NotImplementedError


class OzonBrowserSource(_BrowserSourceBase):
    """Ozon widget source captured from the exact leased task Page."""

    marketplace: MarketplaceName = 'ozon'

    async def _execute_category(
        self,
        request: CategoryRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[CategoryCrawlResult]:
        payload = await self._payload(url, deadline, state)
        validation_state = validate_ozon_payload(payload)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started)
        _require_items(validation_state)
        try:
            products = extract_product_summary_map(
                payload,
                limit=request.limit,
            )
        except Exception:
            raise _parse_drift() from None
        if not products:
            raise _parse_drift()
        result = CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=request.category_slug,
            product_ids=list(products),
            product_urls=[
                item.product_url or ''
                for item in products.values()
            ],
            pre_parsed=products,
        )
        return self._success(result, started, item_count=len(products))

    async def _execute_product(
        self,
        request: ProductRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[ParsedProduct]:
        payload = await self._payload(
            url,
            deadline,
            state,
            not_found_on_404=True,
        )
        validation_state = validate_ozon_payload(payload)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started, product=True)
        _require_items(validation_state)
        try:
            products = extract_product_summary_map(payload, limit=100)
        except Exception:
            raise _parse_drift() from None
        product = products.get(request.product_id)
        if product is None:
            return self._semantic_empty(started, product=True)
        return self._success(product, started, item_count=1)

    async def _execute_search(
        self,
        request: SearchRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        payload = await self._payload(url, deadline, state)
        validation_state = validate_ozon_payload(payload)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started)
        _require_items(validation_state)
        try:
            mapped = extract_product_summary_map(payload, limit=request.limit)
        except Exception:
            raise _parse_drift() from None
        products = tuple(mapped.values())
        if not products:
            raise _parse_drift()
        return self._success(products, started, item_count=len(products))

    async def _payload(
        self,
        target_url: str,
        deadline: OperationDeadline,
        state: _OperationState,
        *,
        not_found_on_404: bool = False,
    ) -> dict[str, Any]:
        try:
            async with self._manager.lease(self.marketplace) as page:
                state.page = page
                self._ensure_active(state, deadline)
                status = await self._navigate(
                    page,
                    OZON_HOME_URL,
                    deadline,
                    state,
                )
                resolution = await self._resolve_challenge(
                    page,
                    deadline,
                    state,
                )
                _raise_for_status(status, resolution=resolution)
                api_url = _ozon_api_url(target_url)
                capture = await self._capture(page, deadline, state, api_url)
                post_resolution = await self._resolve_challenge(
                    page,
                    deadline,
                    state,
                )
                if post_resolution is ChallengeResolution.SOLVED:
                    capture = await self._capture(
                        page,
                        deadline,
                        state,
                        api_url,
                    )
                    await self._resolve_challenge(
                        page,
                        deadline,
                        state,
                    )
                self._ensure_safe_page(page, deadline, state)
                return self._decode_capture(
                    capture,
                    not_found_on_404=not_found_on_404,
                )
        except _SourceFailure:
            raise
        except UnsafeMarketplaceUrl:
            raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            raise _timeout_failure() from None
        except Exception:
            raise _SourceFailure(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            ) from None

    async def _capture(
        self,
        page: PageLike,
        deadline: OperationDeadline,
        state: _OperationState,
        api_url: str,
    ) -> object:
        return await self._evaluate(
            page,
            deadline,
            state,
            _ozon_fetch_expression(api_url, self._max_content_bytes),
            max_bytes=self._max_content_bytes + _CAPTURE_ENVELOPE_BYTES,
        )

    def _decode_capture(
        self,
        capture: object,
        *,
        not_found_on_404: bool,
    ) -> dict[str, Any]:
        if not isinstance(capture, dict):
            raise _parse_drift()
        kind = capture.get('kind')
        if kind == 'redirect':
            raise _SourceFailure(
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_DETECTED,
            )
        if kind == 'unsafe_response':
            raise _invalid_config()
        if kind == 'too_large':
            raise _SourceFailure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.CONTENT_TOO_LARGE,
            )
        if kind == 'invalid_encoding':
            raise _parse_drift()
        if kind not in ('body', 'status'):
            raise _parse_drift()
        status = capture.get('status')
        response_url = capture.get('url')
        if isinstance(status, bool) or not isinstance(status, int):
            raise _parse_drift()
        if not isinstance(response_url, str):
            raise _parse_drift()
        validate_main_frame_url(self.marketplace, response_url)
        _raise_for_status(status, not_found_on_404=not_found_on_404)
        if kind != 'body':
            raise _parse_drift()
        body = capture.get('body')
        if not isinstance(body, str):
            raise _parse_drift()
        try:
            encoded = body.encode('utf-8')
        except UnicodeError:
            raise _parse_drift() from None
        if len(encoded) > self._max_content_bytes:
            raise _SourceFailure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.CONTENT_TOO_LARGE,
            )
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, RecursionError):
            raise _parse_drift() from None
        if not isinstance(payload, dict):
            raise _parse_drift()
        return payload


class WildberriesBrowserSource(_BrowserSourceBase):
    """Wildberries source using the canonical bounded DOM extraction JS."""

    marketplace: MarketplaceName = 'wildberries'

    async def _execute_category(
        self,
        request: CategoryRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[CategoryCrawlResult]:
        snapshot, raw = await self._snapshot(
            url,
            CATEGORY_CARDS_JS,
            deadline,
            state,
        )
        validation_state = validate_wb_dom_snapshot(snapshot)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started)
        _require_items(validation_state)
        products = _map_wb_cards(raw, request.limit)
        result = CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=request.category_slug,
            product_ids=[item.external_id for item in products],
            product_urls=[item.product_url or '' for item in products],
            pre_parsed={item.external_id: item for item in products},
        )
        return self._success(result, started, item_count=len(products))

    async def _execute_product(
        self,
        request: ProductRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[ParsedProduct]:
        snapshot, raw = await self._snapshot(
            url,
            DETAIL_PAGE_JS,
            deadline,
            state,
            not_found_on_404=True,
        )
        validation_state = validate_wb_dom_snapshot(snapshot)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started, product=True)
        _require_items(validation_state)
        if not isinstance(raw, dict):
            raise _parse_drift()
        try:
            product = detail_to_parsed_product(raw, request.product_id)
        except Exception:
            raise _parse_drift() from None
        if product is None:
            raise _parse_drift()
        return self._success(product, started, item_count=1)

    async def _execute_search(
        self,
        request: SearchRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        snapshot, raw = await self._snapshot(
            url,
            CATEGORY_CARDS_JS,
            deadline,
            state,
        )
        validation_state = validate_wb_dom_snapshot(snapshot)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started)
        _require_items(validation_state)
        products = _map_wb_cards(raw, request.limit)
        return self._success(products, started, item_count=len(products))

    async def _snapshot(
        self,
        url: str,
        expression: str,
        deadline: OperationDeadline,
        state: _OperationState,
        *,
        not_found_on_404: bool = False,
    ) -> tuple[str, object]:
        try:
            async with self._manager.lease(self.marketplace) as page:
                state.page = page
                self._ensure_active(state, deadline)
                status = await self._navigate(
                    page,
                    url,
                    deadline,
                    state,
                )
                resolution = await self._resolve_challenge(
                    page,
                    deadline,
                    state,
                )
                _raise_for_status(
                    status,
                    resolution=resolution,
                    not_found_on_404=not_found_on_404,
                )
                snapshot = await self._html(page, deadline, state)
                raw = await self._evaluate(
                    page,
                    deadline,
                    state,
                    expression,
                    open_page_error=_parse_drift(),
                )
                post_resolution = await self._resolve_challenge(
                    page,
                    deadline,
                    state,
                )
                if post_resolution is ChallengeResolution.SOLVED:
                    snapshot = await self._html(page, deadline, state)
                    raw = await self._evaluate(
                        page,
                        deadline,
                        state,
                        expression,
                        open_page_error=_parse_drift(),
                    )
                return snapshot, raw
        except _SourceFailure:
            raise
        except UnsafeMarketplaceUrl:
            raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            raise _timeout_failure() from None
        except Exception:
            raise _SourceFailure(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            ) from None


class YandexMarketBrowserSource(_BrowserSourceBase):
    """Yandex Market source over bounded HTML and canonical JSON-LD helpers."""

    marketplace: MarketplaceName = 'yandex_market'

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._parser = YandexMarketParser()

    async def _execute_category(
        self,
        request: CategoryRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[CategoryCrawlResult]:
        html = await self._snapshot(url, deadline, state)
        validation_state = validate_yandex_html(html)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started)
        _require_items(validation_state)
        products = self._map_products(html, request.limit)
        result = CategoryCrawlResult(
            marketplace=self.marketplace,
            category_slug=request.category_slug,
            product_ids=[item.external_id for item in products],
            product_urls=[item.product_url or '' for item in products],
            pre_parsed={item.external_id: item for item in products},
        )
        return self._success(result, started, item_count=len(products))

    async def _execute_product(
        self,
        request: ProductRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[ParsedProduct]:
        html = await self._snapshot(
            url,
            deadline,
            state,
            not_found_on_404=True,
        )
        validation_state = validate_yandex_html(html)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started, product=True)
        _require_items(validation_state)
        for item in iter_ld_json_products(html):
            product_id = product_id_from_ld_json(item)
            if product_id != request.product_id:
                continue
            try:
                product = self._parser._extract_from_json_ld(
                    item,
                    product_id,
                    html,
                )
            except Exception:
                raise _parse_drift() from None
            return self._success(product, started, item_count=1)
        return self._semantic_empty(started, product=True)

    async def _execute_search(
        self,
        request: SearchRequest,
        url: str,
        started: float,
        deadline: OperationDeadline,
        state: _OperationState,
    ) -> SourceResult[tuple[ParsedProduct, ...]]:
        html = await self._snapshot(url, deadline, state)
        validation_state = validate_yandex_html(html)
        if validation_state is ValidationState.VALID_EMPTY:
            return self._semantic_empty(started)
        _require_items(validation_state)
        products = self._map_products(html, request.limit)
        return self._success(products, started, item_count=len(products))

    async def _snapshot(
        self,
        url: str,
        deadline: OperationDeadline,
        state: _OperationState,
        *,
        not_found_on_404: bool = False,
    ) -> str:
        try:
            async with self._manager.lease(self.marketplace) as page:
                state.page = page
                self._ensure_active(state, deadline)
                status = await self._navigate(
                    page,
                    url,
                    deadline,
                    state,
                )
                resolution = await self._resolve_challenge(
                    page,
                    deadline,
                    state,
                )
                _raise_for_status(
                    status,
                    resolution=resolution,
                    not_found_on_404=not_found_on_404,
                )
                snapshot = await self._html(page, deadline, state)
                post_resolution = await self._resolve_challenge(
                    page,
                    deadline,
                    state,
                )
                if post_resolution is ChallengeResolution.SOLVED:
                    snapshot = await self._html(page, deadline, state)
                return snapshot
        except _SourceFailure:
            raise
        except UnsafeMarketplaceUrl:
            raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            raise _timeout_failure() from None
        except Exception:
            raise _SourceFailure(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            ) from None

    def _map_products(
        self,
        html: str,
        limit: int,
    ) -> tuple[ParsedProduct, ...]:
        products: list[ParsedProduct] = []
        try:
            for item in iter_ld_json_products(html):
                product_id = product_id_from_ld_json(item)
                if product_id is None:
                    raise ValueError('product has no safe routing identifier')
                product = self._parser._extract_from_json_ld(
                    item,
                    product_id,
                    html,
                )
                products.append(product)
                if len(products) >= limit:
                    break
        except Exception:
            raise _parse_drift() from None
        if not products:
            raise _parse_drift()
        return tuple(products)


def _map_wb_cards(raw: object, limit: int) -> tuple[ParsedProduct, ...]:
    if not isinstance(raw, list):
        raise _parse_drift()
    products: list[ParsedProduct] = []
    seen: set[str] = set()
    try:
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError('invalid DOM extraction item')
            product = card_to_parsed_product(item)
            if product is None or product.external_id in seen:
                continue
            seen.add(product.external_id)
            products.append(product)
            if len(products) >= limit:
                break
    except Exception:
        raise _parse_drift() from None
    if not products:
        raise _parse_drift()
    return tuple(products)


def _ozon_api_url(target_url: str) -> str:
    validate_main_frame_url('ozon', target_url)
    parsed = urlsplit(target_url)
    path = parsed.path
    if parsed.query:
        path = f'{path}?{parsed.query}'
    encoded_path = quote(path, safe='/')
    api_url = (
        'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'
        f'?url={encoded_path}'
    )
    return validate_main_frame_url('ozon', api_url)


def _ozon_fetch_expression(api_url: str, max_content_bytes: int) -> str:
    url_literal = json.dumps(api_url)
    # Drop headers the Fetch spec forbids (notably ``User-Agent``): the browser
    # would strip them anyway, so emitting them would only claim a mobile UA
    # the request never sends while the context's own UA goes out instead.
    headers_literal = json.dumps({
        name: value
        for name, value in OZON_MOBILE_HEADERS.items()
        if name.lower() not in _FETCH_FORBIDDEN_HEADERS
    })
    return (
        'async () => {'
        f'const maxBytes = {max_content_bytes};'
        f'const response = await fetch({url_literal}, {{'
        "credentials: 'include',"
        f'headers: {headers_literal},'
        "redirect: 'manual'"
        '});'
        'if (response.redirected || '
        "response.type === 'opaqueredirect' || "
        '(response.status >= 300 && response.status < 400)) {'
        "return {kind: 'redirect'};"
        '}'
        'let finalUrl;'
        'try { finalUrl = new URL(response.url); } '
        "catch (_) { return {kind: 'unsafe_response'}; }"
        'const safeUrl = '
        "finalUrl.protocol === 'https:' && "
        "finalUrl.hostname === 'www.ozon.ru' && "
        "(finalUrl.port === '' || finalUrl.port === '443') && "
        "finalUrl.username === '' && finalUrl.password === '';"
        "if (!safeUrl) { return {kind: 'unsafe_response'}; }"
        'if (response.status < 200 || response.status >= 300) {'
        "return {kind: 'status', status: response.status, "
        'url: response.url};'
        '}'
        "if (!response.body) { return {kind: 'invalid_encoding'}; }"
        'const reader = response.body.getReader();'
        "const decoder = new TextDecoder('utf-8', {fatal: true});"
        "let body = ''; let total = 0;"
        'try {'
        'while (true) {'
        'const chunk = await reader.read();'
        'if (chunk.done) { break; }'
        'if (!(chunk.value instanceof Uint8Array)) {'
        'try { await reader.cancel(); } catch (_) {}'
        "return {kind: 'invalid_encoding'};"
        '}'
        'total += chunk.value.byteLength;'
        'if (total > maxBytes) {'
        'try { await reader.cancel(); } catch (_) {}'
        "return {kind: 'too_large'};"
        '}'
        'body += decoder.decode(chunk.value, {stream: true});'
        '}'
        'body += decoder.decode();'
        '} catch (_) {'
        'try { await reader.cancel(); } catch (_) {}'
        "return {kind: 'invalid_encoding'};"
        '}'
        "return {kind: 'body', status: response.status, "
        'url: response.url, body};'
        '}'
    )


def _raise_for_status(
    status: int,
    *,
    resolution: ChallengeResolution | None = None,
    not_found_on_404: bool = False,
) -> None:
    if status == 404 and not_found_on_404:
        raise _SourceFailure(SourceOutcome.NOT_FOUND, None)
    if status == 429:
        raise _SourceFailure(
            SourceOutcome.RATE_LIMITED,
            SafeErrorCode.RATE_LIMITED,
        )
    if status in (403, 407):
        if resolution is ChallengeResolution.SOLVED:
            return
        raise _SourceFailure(
            SourceOutcome.CHALLENGE,
            SafeErrorCode.CHALLENGE_DETECTED,
        )
    if status >= 500:
        raise _SourceFailure(
            SourceOutcome.TRANSPORT_ERROR,
            SafeErrorCode.TRANSPORT_FAILED,
        )
    if status < 200 or status >= 300:
        raise _parse_drift()


def _require_items(state: ValidationState) -> None:
    if state is ValidationState.CHALLENGE:
        raise _SourceFailure(
            SourceOutcome.CHALLENGE,
            SafeErrorCode.CHALLENGE_DETECTED,
        )
    if state is not ValidationState.VALID_WITH_ITEMS:
        raise _parse_drift()


def _validate_product_id(product_id: str) -> None:
    if (
        not isinstance(product_id, str)
        or not product_id
        or len(product_id) > 30
        or not product_id.isascii()
        or not product_id.isdigit()
        or product_id.startswith('0')
    ):
        raise _invalid_config()


def _validate_limit(limit: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _MAX_ITEMS
    ):
        raise _invalid_config()


def _validate_search_request(request: SearchRequest) -> None:
    _validate_limit(request.limit)
    if (
        isinstance(request.page, bool)
        or not isinstance(request.page, int)
        or not 1 <= request.page <= _MAX_PAGE
    ):
        raise _invalid_config()
    if not isinstance(request.query, str):
        raise _invalid_config()
    query = request.query.strip()
    if not query or len(query) > _MAX_QUERY_LENGTH:
        raise _invalid_config()


def _invalid_config() -> _SourceFailure:
    return _SourceFailure(
        SourceOutcome.INVALID_CONFIG,
        SafeErrorCode.INVALID_CONFIG,
    )


def _parse_drift() -> _SourceFailure:
    return _SourceFailure(
        SourceOutcome.PARSE_DRIFT,
        SafeErrorCode.PARSE_DRIFT,
    )


def _timeout_failure() -> _SourceFailure:
    return _SourceFailure(
        SourceOutcome.TRANSPORT_ERROR,
        SafeErrorCode.TIMEOUT,
    )


def _result(
    outcome: SourceOutcome,
    value: T | None,
    started: float,
    clock: Clock,
    *,
    item_count: int = 0,
    error_code: SafeErrorCode | None = None,
) -> SourceResult[T]:
    return SourceResult(
        source=SourceName.BROWSER,
        outcome=outcome,
        value=value,
        attempt=SourceAttempt(
            source=SourceName.BROWSER,
            outcome=outcome,
            duration_ms=max(0, int((clock() - started) * 1000)),
            item_count=item_count,
            error_code=error_code,
        ),
    )


def _discard_completed_task(task: asyncio.Future[Any]) -> None:
    """Retrieve a finished task's outcome so asyncio never logs it."""
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass


def _ensure_result_within_limit(result: object, limit: int) -> None:
    """Bound the encoded size of an in-page evaluation result."""
    total = 0
    stack: list[tuple[object, int]] = [(result, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > _MAX_RESULT_DEPTH:
            raise _parse_drift()
        if isinstance(item, str):
            try:
                total += len(item.encode('utf-8'))
            except UnicodeError:
                raise _parse_drift() from None
        elif isinstance(item, (bytes, bytearray)):
            total += len(item)
        elif isinstance(item, Mapping):
            total += 2
            for key, value in item.items():
                total += 2
                stack.append((key, depth + 1))
                stack.append((value, depth + 1))
        elif isinstance(item, (list, tuple)):
            total += 2
            for value in item:
                total += 1
                stack.append((value, depth + 1))
        else:
            total += _SCALAR_RESULT_BYTES
        if total > limit:
            raise _SourceFailure(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.CONTENT_TOO_LARGE,
            )


async def _close_safely(page: PageLike) -> None:
    try:
        if not page.is_closed():
            await page.close()
    except Exception:
        pass


__all__ = (
    'OzonBrowserSource',
    'WildberriesBrowserSource',
    'YandexMarketBrowserSource',
)
