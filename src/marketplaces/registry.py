"""Composition of marketplace source chains from trusted configuration.

Category navigation is resolved here, once, from the monitored categories
configuration. Requests never carry a URL: a caller supplies a category slug
and this module hands the browser sources an already validated mapping.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from src.browser.allowlist import UnsafeMarketplaceUrl, validate_main_frame_url
from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.smartcaptcha import SmartCaptchaHandler
from src.core.config import RuntimeRole, Settings
from src.core.config import settings as default_settings
from src.marketplaces.apify_client import ApifyClient
from src.marketplaces.contracts import MarketplaceName, SourceName
from src.marketplaces.sources.apify import ApifySource
from src.marketplaces.sources.browser import (
    BrowserManagerLike,
    OzonBrowserSource,
    WildberriesBrowserSource,
    YandexMarketBrowserSource,
)
from src.marketplaces.sources.public import (
    OzonPublicSource,
    WildberriesPublicSource,
    YandexPublicSource,
)
from src.schemas.deal import CategoriesConfig
from src.services.categories_loader import load_categories_config


log = logging.getLogger(__name__)

CategoryUrls = Mapping[MarketplaceName, Mapping[str, str]]
ManagerFactory = Callable[[], BrowserManagerLike]
CoordinatorFactory = Callable[[], ChallengeCoordinator]

MARKETPLACES: tuple[MarketplaceName, ...] = (
    'wildberries',
    'ozon',
    'yandex_market',
)

_MARKETPLACE_ORIGINS: dict[MarketplaceName, str] = {
    'wildberries': 'https://www.wildberries.ru',
    'ozon': 'https://www.ozon.ru',
    'yandex_market': 'https://market.yandex.ru',
}

_BROWSER_SOURCES: dict[MarketplaceName, type[Any]] = {
    'wildberries': WildberriesBrowserSource,
    'ozon': OzonBrowserSource,
    'yandex_market': YandexMarketBrowserSource,
}


def build_category_urls(config: CategoriesConfig) -> dict[
    MarketplaceName,
    dict[str, str],
]:
    """Map every configured category slug to one allowlisted crawl URL."""
    urls: dict[MarketplaceName, dict[str, str]] = {
        marketplace: {} for marketplace in MARKETPLACES
    }
    for category in config.categories:
        for entry in category.marketplaces:
            marketplace = entry.marketplace
            if marketplace not in urls:
                log.warning(
                    'Unsupported marketplace in category config: %s',
                    marketplace,
                )
                continue
            url = _trusted_url(marketplace, entry.crawl_url)
            if url is None:
                log.warning(
                    'Rejected category URL for %s/%s',
                    marketplace,
                    category.slug,
                )
                continue
            urls[marketplace][category.slug] = url
    return urls


def load_trusted_category_urls() -> dict[MarketplaceName, dict[str, str]]:
    """Read the monitored categories file and validate every crawl URL."""
    return build_category_urls(load_categories_config())


def build_challenge_coordinator(
    settings: Settings = default_settings,
) -> ChallengeCoordinator:
    """Build the coordinator with only explicitly configured handlers."""
    handlers = []
    if settings.captcha_adapter_mode == 'ohmycaptcha':
        handler = _ohmycaptcha_handler()
        if handler is not None:
            handlers.append(handler)
    return ChallengeCoordinator(
        handlers,
        smartcaptcha_handler=SmartCaptchaHandler(
            settings.smartcaptcha_mode,
            widget_id=settings.smartcaptcha_widget_id or None,
        ),
    )


def build_browser_manager(
    role: RuntimeRole,
    settings: Settings = default_settings,
) -> BrowserManagerLike:
    """Build one persistent browser manager for this process role."""
    from src.browser.profiles import BrowserSessionManager, build_sessions

    return BrowserSessionManager(build_sessions(role, settings))


class MarketplaceSourceRegistry:
    """Own one source chain per marketplace and their shared resources."""

    def __init__(
        self,
        *,
        settings: Settings = default_settings,
        manager: BrowserManagerLike | None = None,
        manager_factory: ManagerFactory | None = None,
        coordinator: ChallengeCoordinator | None = None,
        coordinator_factory: CoordinatorFactory | None = None,
        category_urls: CategoryUrls | None = None,
        apify_client: ApifyClient | None = None,
    ) -> None:
        if manager is None and manager_factory is None:
            raise ValueError('registry requires a browser manager or factory')
        self._settings = settings
        self._manager = manager
        self._manager_factory = manager_factory
        self._coordinator = coordinator
        self._coordinator_factory = coordinator_factory
        self._category_urls = category_urls
        self._apify_client = apify_client or ApifyClient(settings)
        self._chains: dict[
            MarketplaceName,
            tuple[tuple[SourceName, Any], ...],
        ] = {}
        self._closed = False

    def source_chain(
        self,
        marketplace: MarketplaceName,
    ) -> tuple[SourceName, ...]:
        """Return the configured source order for one marketplace."""
        return self._settings.source_chain(marketplace)

    def sources_for(
        self,
        marketplace: MarketplaceName,
    ) -> tuple[tuple[SourceName, Any], ...]:
        """Return the ordered adapters backing one marketplace chain."""
        if self._closed:
            raise RuntimeError('marketplace source registry is closed')
        if marketplace not in _MARKETPLACE_ORIGINS:
            raise ValueError(f'unsupported marketplace: {marketplace}')
        cached = self._chains.get(marketplace)
        if cached is not None:
            return cached
        chain = tuple(
            (source, self._build_source(marketplace, source))
            for source in self.source_chain(marketplace)
        )
        self._chains[marketplace] = chain
        return chain

    async def aclose(self) -> None:
        """Release the shared browser manager exactly once."""
        if self._closed:
            return
        self._closed = True
        self._chains.clear()
        manager = self._manager
        self._manager = None
        if manager is None:
            return
        close = getattr(manager, 'close', None)
        if close is None:
            return
        await close()

    def _build_source(
        self,
        marketplace: MarketplaceName,
        source: SourceName,
    ) -> Any:
        if source is SourceName.PUBLIC:
            return _public_source(marketplace)
        if source is SourceName.APIFY:
            return ApifySource(marketplace, self._apify_client)
        return _BROWSER_SOURCES[marketplace](
            self._resolve_manager(),
            self._resolve_coordinator(),
            category_urls=self._marketplace_category_urls(marketplace),
            total_timeout_sec=float(
                self._settings.marketplace_total_timeout_sec,
            ),
            max_content_bytes=self._settings.marketplace_max_content_bytes,
        )

    def _marketplace_category_urls(
        self,
        marketplace: MarketplaceName,
    ) -> Mapping[str, str]:
        if self._category_urls is None:
            self._category_urls = load_trusted_category_urls()
        return self._category_urls.get(marketplace, {})

    def _resolve_manager(self) -> BrowserManagerLike:
        if self._manager is None:
            if self._manager_factory is None:
                raise RuntimeError('browser manager is unavailable')
            self._manager = self._manager_factory()
        return self._manager

    def _resolve_coordinator(self) -> ChallengeCoordinator:
        if self._coordinator is None:
            factory = self._coordinator_factory or build_challenge_coordinator
            self._coordinator = factory()
        return self._coordinator


def build_default_registry(
    role: RuntimeRole,
    settings: Settings = default_settings,
) -> MarketplaceSourceRegistry:
    """Build the production registry for one process role."""
    return MarketplaceSourceRegistry(
        settings=settings,
        manager_factory=lambda: build_browser_manager(role, settings),
        coordinator_factory=lambda: build_challenge_coordinator(settings),
    )


def _public_source(marketplace: MarketplaceName) -> Any:
    if marketplace == 'ozon':
        return OzonPublicSource()
    if marketplace == 'wildberries':
        return WildberriesPublicSource()
    return YandexPublicSource()


def _trusted_url(marketplace: MarketplaceName, raw: str) -> str | None:
    candidate = raw.strip()
    if not candidate:
        return None
    if candidate.startswith('/'):
        candidate = f'{_MARKETPLACE_ORIGINS[marketplace]}{candidate}'
    try:
        return validate_main_frame_url(marketplace, candidate)
    except UnsafeMarketplaceUrl:
        return None


def _ohmycaptcha_handler() -> Any:
    from src.captcha.handlers import OhMyCaptchaHandler
    from src.captcha.ohmycaptcha_adapter import (
        OhMyCaptchaAdapter,
        VendorContractError,
    )

    try:
        return OhMyCaptchaHandler(OhMyCaptchaAdapter())
    except (OSError, VendorContractError):
        log.warning('CAPTCHA adapter unavailable, continuing without it')
        return None


__all__ = (
    'MarketplaceSourceRegistry',
    'build_browser_manager',
    'build_category_urls',
    'build_challenge_coordinator',
    'build_default_registry',
    'load_trusted_category_urls',
)
