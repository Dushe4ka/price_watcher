"""A fixed-input Apify adapter for controlled marketplace fallbacks.

The input and dataset contract in this module are project-owned synthetic
contracts. They have not been validated against a live Apify actor.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TypeAlias, cast

import httpx

from src.core.config import Settings
from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode


HttpClientFactory: TypeAlias = Callable[..., httpx.AsyncClient]
ActorRequest: TypeAlias = CategoryRequest | ProductRequest | SearchRequest

_APIFY_API_BASE_URL = 'https://api.apify.com/v2/acts'
_MAX_RETRY_AFTER_SECONDS = 300
_ACTOR_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_PRODUCT_ID_RE = re.compile(r'^[1-9]\d*$')
_CATEGORY_SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


class _RedactedActorInput(dict[str, object]):
    """A serializable actor payload that never renders sensitive values."""

    def __repr__(self) -> str:
        return '<ApifyActorInput redacted>'

    __str__ = __repr__


class _ContentTooLargeError(RuntimeError):
    """Internal signal for a response that exceeds its configured byte cap."""


class ApifyRateLimitError(MarketplaceSourceError):
    """Typed rate limit failure with a safe bounded server delay hint."""

    def __init__(self, retry_after_seconds: int | None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            SourceOutcome.RATE_LIMITED,
            SafeErrorCode.RATE_LIMITED,
        )


class _ApifyDisabledError(RuntimeError):
    """Signal unavailable optional configuration without rendering details."""

    def __init__(self) -> None:
        super().__init__('Apify fallback is disabled')


class ApifyClient:
    """Run one configured actor without retrying or logging request data."""

    def __init__(
        self,
        settings: Settings,
        client_factory: HttpClientFactory = httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    def is_enabled(
        self,
        marketplace: MarketplaceName,
        operation: MarketplaceOperation,
    ) -> bool:
        """Return whether a token and this operation's actor are configured."""
        token = self._settings.apify_api_token.get_secret_value().strip()
        actor_id = self._settings.apify_actor_id(marketplace, operation)
        return bool(token and actor_id)

    async def run_actor(
        self,
        marketplace: MarketplaceName,
        operation: MarketplaceOperation,
        request: ActorRequest,
    ) -> list[dict[str, object]]:
        """Run the configured actor and return its validated dataset items."""
        actor_id = self._actor_id(marketplace, operation)
        token = self._settings.apify_api_token.get_secret_value().strip()
        if not token or not actor_id:
            raise _ApifyDisabledError()

        payload = build_actor_input(marketplace, operation, request)
        url = f'{_APIFY_API_BASE_URL}/{actor_id}/run-sync-get-dataset-items'
        headers = {'Authorization': f'Bearer {token}'}
        transport_failed = False
        content_too_large = False
        try:
            dataset_body = await self._read_dataset_body(
                url,
                headers,
                payload,
            )
        except _ContentTooLargeError:
            content_too_large = True
            dataset_body = b''
        except httpx.HTTPError:
            transport_failed = True
            dataset_body = b''

        if content_too_large:
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.CONTENT_TOO_LARGE,
            )
        if transport_failed:
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

        try:
            dataset = json.loads(dataset_body)
        except (TypeError, ValueError):
            dataset = None
        if dataset is None:
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )
        if not isinstance(dataset, list) or any(
            not isinstance(item, dict) for item in dataset
        ):
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )
        return [cast(dict[str, object], item) for item in dataset]

    async def _read_dataset_body(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> bytes:
        max_content_bytes = self._settings.marketplace_max_content_bytes
        async with self._client_factory(
            headers=headers,
            timeout=self._settings.marketplace_total_timeout_sec,
        ) as client:
            async with client.stream('POST', url, json=payload) as response:
                self._raise_for_status(response)
                declared_size = _content_length(response.headers.get(
                    'Content-Length',
                ))
                if (
                    declared_size is not None
                    and declared_size > max_content_bytes
                ):
                    raise _ContentTooLargeError()
                total_size = 0
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    total_size += len(chunk)
                    if total_size > max_content_bytes:
                        raise _ContentTooLargeError()
                    chunks.append(chunk)
        return b''.join(chunks)

    def _actor_id(
        self,
        marketplace: MarketplaceName,
        operation: MarketplaceOperation,
    ) -> str:
        actor_id = self._settings.apify_actor_id(
            marketplace,
            operation,
        ).strip()
        if not actor_id:
            return ''
        if not _ACTOR_ID_RE.fullmatch(actor_id):
            raise ValueError('invalid Apify actor configuration')
        return actor_id

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise MarketplaceSourceError(
                SourceOutcome.AUTH_ERROR,
                SafeErrorCode.AUTH_FAILED,
            )
        if response.status_code == 429:
            raise ApifyRateLimitError(
                _bounded_retry_after(response.headers.get('Retry-After')),
            )
        if response.status_code >= 500:
            raise MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )
        if response.status_code >= 400:
            raise MarketplaceSourceError(
                SourceOutcome.INVALID_CONFIG,
                SafeErrorCode.INVALID_CONFIG,
            )
        if response.status_code != 200:
            raise MarketplaceSourceError(
                SourceOutcome.PARSE_DRIFT,
                SafeErrorCode.PARSE_DRIFT,
            )


def build_actor_input(
    marketplace: MarketplaceName,
    operation: MarketplaceOperation,
    request: ActorRequest,
) -> dict[str, object]:
    """Build a fixed actor input from a typed request and known hosts only."""
    if operation is MarketplaceOperation.SEARCH_PRODUCTS:
        if not isinstance(request, SearchRequest):
            raise ValueError('invalid Apify operation input')
        if request.limit < 1 or request.page < 1 or not request.query.strip():
            raise ValueError('invalid Apify operation input')
        return _RedactedActorInput({
            'searchQuery': request.query,
            'page': request.page,
            'maxItems': request.limit,
        })
    if operation is MarketplaceOperation.PARSE_PRODUCT:
        if not isinstance(request, ProductRequest):
            raise ValueError('invalid Apify operation input')
        return _RedactedActorInput({
            'productUrl': _product_url(marketplace, request.product_id),
            'maxItems': 1,
        })
    if operation is MarketplaceOperation.CRAWL_CATEGORY:
        if not isinstance(request, CategoryRequest) or request.limit < 1:
            raise ValueError('invalid Apify operation input')
        return _RedactedActorInput({
            'categoryUrl': _category_url(marketplace, request.category_slug),
            'maxItems': request.limit,
        })
    raise ValueError('invalid Apify operation input')


def _product_url(marketplace: MarketplaceName, product_id: str) -> str:
    if not _PRODUCT_ID_RE.fullmatch(product_id):
        raise ValueError('invalid Apify operation input')
    if marketplace == 'wildberries':
        return f'https://www.wildberries.ru/catalog/{product_id}/detail.aspx'
    if marketplace == 'ozon':
        return f'https://www.ozon.ru/product/{product_id}/'
    if marketplace == 'yandex_market':
        return f'https://market.yandex.ru/card/x/{product_id}'
    raise ValueError('invalid Apify operation input')


def _category_url(marketplace: MarketplaceName, category_slug: str) -> str:
    if not _CATEGORY_SLUG_RE.fullmatch(category_slug):
        raise ValueError('invalid Apify operation input')
    if marketplace == 'wildberries':
        return f'https://www.wildberries.ru/catalog/{category_slug}/'
    if marketplace == 'ozon':
        return f'https://www.ozon.ru/category/{category_slug}/'
    if marketplace == 'yandex_market':
        return f'https://market.yandex.ru/catalog--{category_slug}/'
    raise ValueError('invalid Apify operation input')


def _bounded_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lstrip('0') or '0'
    if not normalized.isdigit():
        return None
    if len(normalized) > len(str(_MAX_RETRY_AFTER_SECONDS)):
        return _MAX_RETRY_AFTER_SECONDS
    try:
        return min(int(normalized), _MAX_RETRY_AFTER_SECONDS)
    except (OverflowError, ValueError):
        return None


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lstrip('0') or '0'
    if not normalized.isdigit():
        return None
    if len(normalized) > 10:
        return 10_485_761
    try:
        return int(normalized)
    except (OverflowError, ValueError):
        return None
