"""Application-facing marketplace operations over configured source chains.

The service owns no retry, no source selection and no challenge handling of
its own: it builds the ordered :class:`SourceCall` sequence for one
marketplace and hands it to :func:`execute_fallback`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.core.config import RuntimeRole
from src.core.config import settings as default_settings
from src.crawlers.base import CategoryCrawlResult
from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    MarketplaceOperation,
    MarketplaceResult,
    ProductRequest,
    SearchRequest,
    SourceResult,
)
from src.marketplaces.fallback import SourceCall, execute_fallback
from src.marketplaces.registry import (
    MARKETPLACES,
    MarketplaceSourceRegistry,
    build_default_registry,
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
    ) -> None:
        if marketplace not in MARKETPLACES:
            raise ValueError(f'unsupported marketplace: {marketplace}')
        self._marketplace: MarketplaceName = marketplace
        self._registry = registry

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
        calls = tuple(
            SourceCall(source=name, invoke=_bind(invoke, source, request))
            for name, source in self._registry.sources_for(self._marketplace)
        )
        return await execute_fallback(self._marketplace, operation, calls)


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
)
