"""Application-facing marketplace operations over configured source chains.

The service is the retry composition point: it builds the ordered
:class:`SourceCall` sequence for one marketplace, wraps every call in the
single :class:`SourceRetryExecutor` owning internal transport retries, binds
them all to one shared :class:`OperationDeadline`, and hands the sequence to
:func:`execute_fallback`, which still runs each source exactly once. Source
selection and challenge handling remain owned elsewhere.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.core.config import RuntimeRole, Settings
from src.core.config import settings as default_settings
from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    MarketplaceOperation,
    MarketplaceResult,
    ProductRequest,
    SearchRequest,
    SourceName,
    SourceResult,
)
from src.marketplaces.fallback import SourceCall, execute_fallback
from src.marketplaces.registry import (
    MARKETPLACES,
    MarketplaceSourceRegistry,
    build_default_registry,
)
from src.marketplaces.retry import (
    Clock,
    OperationDeadline,
    RetryPolicy,
    Sleep,
    SourceRetryExecutor,
)
from src.parsers.base import ParsedProduct


T = TypeVar('T')
Request = CategoryRequest | ProductRequest | SearchRequest
SourceInvoke = Callable[[Any, Any], Awaitable[SourceResult[Any]]]

_registry: MarketplaceSourceRegistry | None = None
_services: dict[MarketplaceName, 'MarketplaceService'] = {}
_runtime_role: RuntimeRole | None = None
_shut_down = False


class MarketplaceService:
    """Run one marketplace operation over its configured fallback chain."""

    def __init__(
        self,
        marketplace: MarketplaceName,
        registry: MarketplaceSourceRegistry,
        *,
        settings: Settings = default_settings,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        if marketplace not in MARKETPLACES:
            raise ValueError(f'unsupported marketplace: {marketplace}')
        self._marketplace: MarketplaceName = marketplace
        self._registry = registry
        self._settings = settings
        self._sleep = sleep
        self._clock = clock
        self._executor = SourceRetryExecutor()

    @property
    def marketplace(self) -> MarketplaceName:
        """Return the marketplace this service is bound to."""
        return self._marketplace

    async def crawl_category(
        self,
        request: CategoryRequest,
    ) -> MarketplaceResult[CategoryCrawlResult]:
        """Crawl one trusted category slug; requests never carry a URL."""
        return await self._run(
            MarketplaceOperation.CRAWL_CATEGORY,
            request,
            _invoke_crawl_category,
        )

    async def parse_product(
        self,
        request: ProductRequest,
    ) -> MarketplaceResult[ParsedProduct]:
        """Parse one product card over the configured source chain."""
        return await self._run(
            MarketplaceOperation.PARSE_PRODUCT,
            request,
            _invoke_parse_product,
        )

    async def search_products(
        self,
        request: SearchRequest,
    ) -> MarketplaceResult[tuple[ParsedProduct, ...]]:
        """Search products over the configured source chain."""
        return await self._run(
            MarketplaceOperation.SEARCH_PRODUCTS,
            request,
            _invoke_search_products,
        )

    async def aclose(self) -> None:
        """Release the shared registry resources exactly once."""
        await self._registry.aclose()

    async def _run(
        self,
        operation: MarketplaceOperation,
        request: Request,
        invoke: SourceInvoke,
    ) -> MarketplaceResult[Any]:
        """Run one operation over its configured chain under one deadline.

        The shared deadline is built from
        ``marketplace_operation_timeout_sec``, which must stay strictly
        larger than any single source's own per-invocation timeout
        (``marketplace_total_timeout_sec``, used by the browser sources in
        ``registry.py`` and by Apify's HTTP client in ``apify_client.py``).
        Reusing the per-source setting here was the bug: a source that
        consumes its full per-source budget would leave the shared
        deadline already expired by the time the next source in the chain
        is due, so that next source -- e.g. Apify, the fallback for a
        browser wall -- would never run at all.
        """
        deadline = OperationDeadline.from_timeout_ms(
            self._settings.marketplace_operation_timeout_sec * 1000,
            self._clock,
        )
        calls = tuple(
            self._retrying_call(name, source, request, invoke, deadline)
            for name, source in self._registry.sources_for(self._marketplace)
        )
        return await execute_fallback(self._marketplace, operation, calls)

    def _retrying_call(
        self,
        name: SourceName,
        source: Any,
        request: Request,
        invoke: SourceInvoke,
        deadline: OperationDeadline,
    ) -> SourceCall[Any]:
        """Wrap one source call in the executor owning transport retries.

        ``execute_fallback`` still invokes the returned call exactly once;
        the bounded retry happens inside that single invocation, against the
        deadline shared by every source in the chain.
        """
        inner = SourceCall(
            source=name,
            invoke=_bind(invoke, source, request),
        )
        policy = self._retry_policy(name)

        async def call() -> SourceResult[Any]:
            return await self._executor.run(
                inner,
                policy,
                self._sleep,
                self._clock,
                deadline,
            )

        return SourceCall(source=name, invoke=call)

    def _retry_policy(self, name: SourceName) -> RetryPolicy:
        """Return the transport retry budget configured for one source.

        The Apify source performs exactly one attempt by construction, so
        its policy makes the executor a transparent pass-through that still
        honours the shared deadline and reports a measured attempt count.
        """
        if name is SourceName.APIFY:
            return RetryPolicy(max_attempts=1)
        settings = self._settings
        return RetryPolicy(
            max_attempts=settings.marketplace_retry_max_attempts,
            base_delay_ms=settings.marketplace_retry_base_delay_ms,
            max_delay_ms=settings.marketplace_retry_max_delay_ms,
        )


def configure_marketplace_runtime(role: RuntimeRole) -> None:
    """Record the process role used for future default composition."""
    global _runtime_role
    _runtime_role = role


def marketplace_runtime_role() -> RuntimeRole:
    """Return the role the composition root builds browser profiles for."""
    return _runtime_role or default_settings.runtime_role


def configure_marketplace_registry(
    registry: MarketplaceSourceRegistry | None,
) -> None:
    """Install an explicit registry, or reset composition when ``None``."""
    global _registry, _shut_down
    _registry = registry
    _services.clear()
    _shut_down = False


async def start_marketplace_services() -> None:
    """Compose and validate shared marketplace resources at process boot.

    Call once from the composition root before the process serves requests
    or starts polling. A misconfigured worker count or a persistent profile
    already owned by another process fails here, loudly, instead of being
    caught per request and reported as a generic transport error.
    """
    global _registry
    if _shut_down:
        raise RuntimeError('marketplace services are shut down')
    if _registry is None:
        _registry = build_default_registry(marketplace_runtime_role())
    await _registry.start()


def refresh_marketplace_category_urls() -> None:
    """Rebuild the trusted category map from configuration, once per run.

    A no-op until something has composed a registry, because a registry
    built later reads the configuration afresh anyway.
    """
    if _shut_down or _registry is None:
        return
    _registry.refresh_category_urls()


def get_marketplace_service(
    marketplace: MarketplaceName,
) -> MarketplaceService:
    """Return the lazily composed service for one marketplace."""
    global _registry
    if _shut_down:
        raise RuntimeError('marketplace services are shut down')
    cached = _services.get(marketplace)
    if cached is not None:
        return cached
    if _registry is None:
        _registry = build_default_registry(marketplace_runtime_role())
    service = MarketplaceService(marketplace, _registry)
    _services[marketplace] = service
    return service


async def close_marketplace_services() -> None:
    """Close shared marketplace resources exactly once per process."""
    global _registry, _shut_down
    registry = _registry
    _registry = None
    _services.clear()
    _shut_down = True
    if registry is None:
        return
    await registry.aclose()


async def _invoke_crawl_category(
    source: Any,
    request: CategoryRequest,
) -> SourceResult[CategoryCrawlResult]:
    return await source.crawl_category(request)


async def _invoke_parse_product(
    source: Any,
    request: ProductRequest,
) -> SourceResult[ParsedProduct]:
    return await source.parse_product(request)


async def _invoke_search_products(
    source: Any,
    request: SearchRequest,
) -> SourceResult[tuple[ParsedProduct, ...]]:
    return await source.search_products(request)


def _bind(
    invoke: SourceInvoke,
    source: Any,
    request: Request,
) -> Callable[[], Awaitable[SourceResult[Any]]]:
    async def call() -> SourceResult[Any]:
        return await invoke(source, request)

    return call


__all__ = (
    'MarketplaceService',
    'close_marketplace_services',
    'configure_marketplace_registry',
    'configure_marketplace_runtime',
    'get_marketplace_service',
    'marketplace_runtime_role',
    'refresh_marketplace_category_urls',
    'start_marketplace_services',
)
