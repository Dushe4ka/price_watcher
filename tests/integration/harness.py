"""Controlled real-browser harness over the production marketplace stack.

Design note — why there is no test allowlist here
=================================================

``src.browser.allowlist`` is the security boundary of the whole marketplace
stack, so this harness does not touch it: no host is added, no scheme rule is
relaxed and no test-only validation path exists. The code under test really
navigates to ``https://market.yandex.ru/...`` and really re-validates
``page.url`` against the untouched production allowlist.

Only the *bytes* are controlled. Playwright's ``BrowserContext.route``
intercepts every request the context makes and serves it from the loopback
fixture server, and every request whose host is not part of the controlled
topology is aborted — so no test can reach a live marketplace even by
accident.

Two belts, not one
==================

Route interception alone is not sufficient. Playwright does not pause the
follow-up request of a redirect it fulfilled itself, so a fulfilled ``302``
would let the browser reach the real host. Every controlled context is
therefore also launched behind a blackhole proxy on a closed loopback port:
anything that ever escapes interception fails to connect instead of leaving
the machine. The fixture scenarios avoid fulfilled HTTP redirects entirely
and use renderer-initiated navigation, which *is* intercepted.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from playwright.async_api import async_playwright

from src.browser.allowlist import validate_main_frame_url
from src.browser.profiles import (
    BrowserSessionManager,
    ProfileLock,
    validate_single_browser_worker,
)
from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.models import ChallengeDetection, ChallengeType
from src.core.browser_proxy import STEALTH_INIT_SCRIPT, chromium_runtime_args
from src.marketplaces.contracts import (
    MarketplaceName,
    MarketplaceResult,
    ProductRequest,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.service import MarketplaceService
from src.marketplaces.sources.browser import YandexMarketBrowserSource
from tests.integration.fixture_server import (
    UNSAFE_REDIRECT_HOST,
    FixtureServer,
    fixture_server,
)
from tests.marketplace_service_fakes import StubRegistry, retry_settings


# The controlled topology deliberately mirrors production hosts exactly.
MARKETPLACE_HOSTS: dict[MarketplaceName, str] = {
    'ozon': 'www.ozon.ru',
    'wildberries': 'www.wildberries.ru',
    'yandex_market': 'market.yandex.ru',
}

DEFAULT_MARKETPLACE: MarketplaceName = 'yandex_market'
DEFAULT_PRODUCT_ID = '1017'
SKIP_ENV_VAR = 'PRICE_WATCHER_SKIP_BROWSER_TESTS'

# The discard port is closed on every supported platform, so a request that
# escaped route interception fails to connect rather than leaving the host.
BLACKHOLE_PROXY = {'server': 'http://127.0.0.1:9'}

_FORWARDED_HEADERS = frozenset(
    ('content-type', 'location', 'retry-after'),
)


class ControlledRouter:
    """Serve controlled hosts from the fixture and abort everything else."""

    def __init__(
        self,
        server: FixtureServer,
        marketplace: MarketplaceName = DEFAULT_MARKETPLACE,
    ) -> None:
        self._server = server
        self._session: aiohttp.ClientSession | None = None
        self._overrides = {
            MARKETPLACE_HOSTS[marketplace]: None,
            UNSAFE_REDIRECT_HOST: '/attacker',
        }
        self.hosts: list[str] = []
        self.aborted: list[str] = []
        self.pages: list[Any] = []

    async def handle(self, route: Any) -> None:
        """Fulfil one intercepted request from the loopback fixture."""
        try:
            await self._handle(route)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A deadline test tears the stack down while a request is still
            # paused. Aborting it keeps teardown quiet without ever letting
            # the request continue to a real host.
            try:
                await route.abort()
            except Exception:
                return

    async def _handle(self, route: Any) -> None:
        request = route.request
        parsed = urlsplit(request.url)
        host = parsed.hostname or ''
        self.hosts.append(host)
        self._record_page(request)
        if host not in self._overrides:
            self.aborted.append(host)
            await route.abort()
            return
        if parsed.path == '/favicon.ico':
            await route.fulfill(status=404, body=b'')
            return
        override = self._overrides[host]
        path = override or _fixture_path(parsed.path, parsed.query)
        status, headers, body = await self._fetch(path)
        await route.fulfill(status=status, headers=headers, body=body)

    async def aclose(self) -> None:
        """Close the fixture HTTP client owned by this router."""
        session = self._session
        self._session = None
        if session is not None:
            await session.close()

    def _record_page(self, request: Any) -> None:
        try:
            page = request.frame.page
        except Exception:
            return
        if page is not None:
            self.pages.append(page)

    async def _fetch(
        self,
        path: str,
    ) -> tuple[int, dict[str, str], bytes]:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.get(
            self._server.url(path),
            allow_redirects=False,
        ) as response:
            body = await response.read()
            headers = {
                name: value
                for name, value in response.headers.items()
                if name.lower() in _FORWARDED_HEADERS
            }
            return response.status, headers, body


class ControlledBrowserSession:
    """A real persistent Chromium session bound to a temporary profile."""

    def __init__(
        self,
        profile_dir: Path,
        router: ControlledRouter,
    ) -> None:
        self._profile_dir = profile_dir
        self._router = router
        self._playwright: Any = None
        self._context: Any = None
        self._profile_lock: ProfileLock | None = None
        self._last_used = 0.0

    async def ensure_context(self) -> Any:
        """Open the persistent context lazily, exactly once per session."""
        if self._context is None:
            await self._start()
        self.touch()
        return self._context

    async def close_if_idle(self) -> None:
        """Controlled runs never idle out inside a single test."""
        return None

    def touch(self) -> None:
        """Record use, mirroring the production session contract."""
        self._last_used = time.monotonic()

    async def close(self) -> None:
        """Close the context, stop the driver and release the profile."""
        context = self._context
        playwright = self._playwright
        profile_lock = self._profile_lock
        self._context = None
        self._playwright = None
        self._profile_lock = None
        try:
            if context is not None:
                await context.close()
            if playwright is not None:
                await playwright.stop()
        finally:
            if profile_lock is not None:
                profile_lock.release()

    async def _start(self) -> None:
        validate_single_browser_worker()
        self._profile_lock = ProfileLock(self._profile_dir)
        self._profile_lock.acquire()
        try:
            self._playwright = await async_playwright().start()
            self._context = (
                await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    headless=True,
                    proxy=BLACKHOLE_PROXY,
                    args=chromium_runtime_args(),
                    locale='ru-RU',
                    timezone_id='Europe/Moscow',
                    viewport={'width': 1280, 'height': 800},
                )
            )
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
            await self._context.route('**/*', self._router.handle)
            for page in tuple(self._context.pages):
                if not page.is_closed():
                    await page.close()
        except BaseException:
            await self.close()
            raise


class InPageWidgetHandler:
    """Resolve the fixture's antibot widget on the leased page only.

    This is a test handler for a test wall: production ships no handler that
    can resolve a real marketplace wall offline. What it proves is the
    production contract around it — the coordinator hands the handler the
    exact leased page, re-detects afterwards on that same page and only then
    reports ``SOLVED``.
    """

    def __init__(self) -> None:
        self.pages: list[Any] = []

    def supports(self, detection: ChallengeDetection) -> bool:
        """Support only the non-interactive widget the fixture serves."""
        return (
            not detection.is_interactive
            and detection.challenge_type is ChallengeType.TURNSTILE
        )

    async def handle(
        self,
        page: Any,
        detection: ChallengeDetection,
        *,
        timeout_ms: float,
    ) -> None:
        """Click the widget control on the page the coordinator supplied."""
        if not self.supports(detection):
            return
        self.pages.append(page)
        await page.locator('#verify-box').click(timeout=timeout_ms)

    def __repr__(self) -> str:
        return 'InPageWidgetHandler(scope=leased_page_only)'


@asynccontextmanager
async def controlled_stack(
    server: FixtureServer,
    *,
    profile_dir: Path,
    marketplace: MarketplaceName = DEFAULT_MARKETPLACE,
    total_timeout_sec: float = 10.0,
    max_content_bytes: int = 2_000_000,
) -> AsyncIterator[tuple[Any, ControlledRouter, InPageWidgetHandler]]:
    """Compose the production stack over one controlled browser session."""
    router = ControlledRouter(server, marketplace)
    session = ControlledBrowserSession(profile_dir, router)
    manager = BrowserSessionManager({marketplace: session})
    handler = InPageWidgetHandler()
    coordinator = ChallengeCoordinator([handler])
    source = YandexMarketBrowserSource(
        manager,
        coordinator,
        total_timeout_sec=total_timeout_sec,
        max_content_bytes=max_content_bytes,
    )
    try:
        yield source, router, handler
    finally:
        try:
            await manager.close()
        finally:
            await router.aclose()


async def run_controlled_browser_flow(
    server: FixtureServer,
    *,
    profile_dir: Path,
    product_id: str = DEFAULT_PRODUCT_ID,
    total_timeout_sec: float = 10.0,
    max_content_bytes: int = 2_000_000,
) -> tuple[SourceResult[Any], tuple[int, ...]]:
    """Run one real browser operation and report observed page identities."""
    async with controlled_stack(
        server,
        profile_dir=profile_dir,
        total_timeout_sec=total_timeout_sec,
        max_content_bytes=max_content_bytes,
    ) as (source, router, handler):
        result = await source.parse_product(
            ProductRequest(product_id=product_id),
        )
        identities = tuple(
            id(page) for page in (*router.pages, *handler.pages)
        )
    return result, identities


async def run_controlled_retry_flow(
    *,
    max_attempts: int = 2,
    profile_dir: Path,
    server: FixtureServer | None = None,
) -> tuple[dict[str, int], MarketplaceResult[Any]]:
    """Run the real service over a full chain, counting attempts exactly.

    The chain is driven by the production :class:`MarketplaceService`, whose
    own composition owns every retry; the browser attempts are counted at
    the fixture server socket.
    """
    if server is not None:
        return await _retry_flow(server, max_attempts, profile_dir)
    async with fixture_server('transport-error') as owned:
        return await _retry_flow(owned, max_attempts, profile_dir)


async def run_lease_navigation(
    server: FixtureServer,
    *,
    profile_dir: Path,
    expected_url: str,
    marketplace: MarketplaceName = DEFAULT_MARKETPLACE,
) -> tuple[BaseException | None, tuple[str, ...]]:
    """Drive one production lease through a real client-side navigation.

    Returns whatever the lease raised on exit plus every host the controlled
    router saw, so a caller can assert both the decision and the fact that
    the off-host response really was available to be followed.
    """
    router = ControlledRouter(server, marketplace)
    session = ControlledBrowserSession(profile_dir, router)
    manager = BrowserSessionManager({marketplace: session})
    url = _marketplace_product_url(marketplace)
    raised: BaseException | None = None
    try:
        async with manager.lease(marketplace) as page:
            await page.goto(
                url,
                wait_until='domcontentloaded',
                timeout=10_000,
            )
            validate_main_frame_url(marketplace, page.url)
            try:
                await page.wait_for_url(expected_url, timeout=10_000)
            except Exception:
                # The lease guard may already have closed the page; the
                # navigation still happened, which is what matters here.
                pass
    except BaseException as exc:  # noqa: BLE001 - the decision under test
        raised = exc
    finally:
        try:
            await manager.close()
        finally:
            await router.aclose()
    return raised, tuple(router.hosts)


async def seed_profile_state(
    server: FixtureServer,
    *,
    profile_dir: Path,
    cookie: str,
    storage: tuple[str, str],
) -> None:
    """Write a cookie and a localStorage entry through a real browser."""
    key, value = storage
    async with _leased_page(server, profile_dir) as page:
        await page.evaluate(
            '([cookie, key, value]) => {'
            'document.cookie = cookie + "; path=/; max-age=600";'
            'window.localStorage.setItem(key, value);'
            '}',
            [cookie, key, value],
        )


async def read_profile_state(
    server: FixtureServer,
    *,
    profile_dir: Path,
    storage_key: str,
) -> dict[str, str]:
    """Read cookie and localStorage back after a manager restart."""
    async with _leased_page(server, profile_dir) as page:
        state = await page.evaluate(
            '(key) => ({'
            'cookie: document.cookie,'
            'storage: window.localStorage.getItem(key) || "",'
            '})',
            storage_key,
        )
    return {
        'cookie': str(state.get('cookie', '')),
        'storage': str(state.get('storage', '')),
    }


class RealBrowserTestCase(unittest.IsolatedAsyncioTestCase):
    """Base case owning a temporary profile root and worker isolation."""

    def setUp(self) -> None:
        if os.environ.get(SKIP_ENV_VAR) == '1':
            self.skipTest('real-browser integration tests disabled by env')
        self._temp = tempfile.TemporaryDirectory(prefix='pw-task15-')
        self.addCleanup(self._temp.cleanup)
        self._previous_concurrency = os.environ.get('WEB_CONCURRENCY')
        os.environ['WEB_CONCURRENCY'] = '1'
        self.addCleanup(self._restore_concurrency)
        self._profile_index = 0

    def profile_dir(self) -> Path:
        """Return a fresh temporary persistent-profile directory."""
        self._profile_index += 1
        path = Path(self._temp.name) / f'profile-{self._profile_index}'
        return path

    def _restore_concurrency(self) -> None:
        if self._previous_concurrency is None:
            os.environ.pop('WEB_CONCURRENCY', None)
        else:
            os.environ['WEB_CONCURRENCY'] = self._previous_concurrency


@asynccontextmanager
async def _leased_page(
    server: FixtureServer,
    profile_dir: Path,
    marketplace: MarketplaceName = DEFAULT_MARKETPLACE,
) -> AsyncIterator[Any]:
    """Lease one production-managed page over a real persistent profile."""
    router = ControlledRouter(server, marketplace)
    session = ControlledBrowserSession(profile_dir, router)
    manager = BrowserSessionManager({marketplace: session})
    url = _marketplace_product_url(marketplace)
    try:
        async with manager.lease(marketplace) as page:
            await page.goto(url, wait_until='domcontentloaded', timeout=10_000)
            validate_main_frame_url(marketplace, page.url)
            yield page
    finally:
        try:
            await manager.close()
        finally:
            await router.aclose()


@dataclass(slots=True)
class _CountingSource:
    """A source adapter that only records how many times it was called."""

    source: SourceName
    calls: list[str] = field(default_factory=list)

    async def parse_product(
        self,
        request: ProductRequest,
    ) -> SourceResult[Any]:
        """Return a retriable transport failure and count the attempt."""
        del request
        self.calls.append(self.source.value)
        return SourceResult(
            source=self.source,
            outcome=SourceOutcome.TRANSPORT_ERROR,
            value=None,
            attempt=SourceAttempt(
                source=self.source,
                outcome=SourceOutcome.TRANSPORT_ERROR,
                duration_ms=0,
                item_count=0,
                error_code=SafeErrorCode.TRANSPORT_FAILED,
            ),
        )


async def _retry_flow(
    server: FixtureServer,
    max_attempts: int,
    profile_dir: Path,
) -> tuple[dict[str, int], MarketplaceResult[Any]]:
    public = _CountingSource(SourceName.PUBLIC)
    apify = _CountingSource(SourceName.APIFY)
    # The real production composition root is what is under test here, so
    # the chain is handed to ``MarketplaceService`` exactly as the registry
    # hands it over and nothing here wires a retry of its own. Only the
    # retry configuration is overridden: ``max_attempts`` is the variable
    # under test and zero delay keeps the suite free of wall-clock sleeps
    # without weakening the count.
    settings = retry_settings(
        max_attempts=max_attempts,
        base_delay_ms=0,
        max_delay_ms=0,
        total_timeout_sec=60,
    )

    async with controlled_stack(
        server,
        profile_dir=profile_dir,
        total_timeout_sec=10.0,
    ) as (source, _router, _handler):
        registry = StubRegistry(
            (
                (SourceName.PUBLIC, public),
                (SourceName.BROWSER, source),
                (SourceName.APIFY, apify),
            ),
        )
        service = MarketplaceService(
            DEFAULT_MARKETPLACE,
            registry,
            settings=settings,
        )
        result = await service.parse_product(
            ProductRequest(product_id=DEFAULT_PRODUCT_ID),
        )
        browser_requests = server.count()

    counters = {
        'public': len(public.calls),
        'browser': browser_requests,
        'apify': len(apify.calls),
    }
    return counters, result


def _marketplace_product_url(marketplace: MarketplaceName) -> str:
    """Build the exact production URL and validate it as production does."""
    return validate_main_frame_url(
        marketplace,
        f'https://{MARKETPLACE_HOSTS[marketplace]}/card/x/'
        f'{DEFAULT_PRODUCT_ID}',
    )


def _fixture_path(path: str, query: str) -> str:
    return f'{path}?{query}' if query else path


__all__ = (
    'ControlledBrowserSession',
    'ControlledRouter',
    'InPageWidgetHandler',
    'MARKETPLACE_HOSTS',
    'RealBrowserTestCase',
    'controlled_stack',
    'read_profile_state',
    'run_controlled_browser_flow',
    'run_controlled_retry_flow',
    'run_lease_navigation',
    'seed_profile_state',
)
