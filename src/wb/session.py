from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from src.browser.allowlist import validate_main_frame_url
from src.browser.profiles import ProfileLock, validate_single_browser_worker
from src.core.browser_proxy import (
    STEALTH_INIT_SCRIPT,
    chromium_runtime_args,
    playwright_proxy_config,
)
from src.core.config import settings
from src.parsers.utils import BlockedError
from src.wb.constants import WB_DESKTOP_UA

logger = logging.getLogger(__name__)

# WB's own antibot challenge stalls under headless Chromium but normally
# resolves under a real headed session. Display-less deployments provide Xvfb.
_PLACEHOLDER_TITLES = ('', '...')


class WBBrowserSession:
    """Headed Playwright persistent Chromium session for Wildberries."""

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        playwright_factory: Callable[[], Any] | None = None,
        idle_sec: int | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._context_closed = True
        self._playwright_stopped = True
        self._profile_dir = profile_dir or settings.profile_dir(
            settings.runtime_role,
            'wildberries',
        )
        self._playwright_factory = playwright_factory or async_playwright
        self._idle_sec = (
            settings.wb_browser_idle_sec if idle_sec is None else idle_sec
        )
        self._profile_lock: ProfileLock | None = None
        self._last_used_monotonic = 0.0
        self._proxy_cycle: itertools.cycle[str] | None = None
        self._current_proxy: str | None = None
        self._consecutive_blocks = 0
        self._cooldown_until = 0.0

    async def ensure_context(self) -> BrowserContext:
        """Return the persistent context, opening it lazily."""
        async with self._lock:
            await self._respect_cooldown()
            await self._close_if_idle_inner()
            return await self._ensure_context_inner()

    async def ensure_page(self) -> Page:
        """Preserve the legacy client API until Task 11 migration."""
        async with self._lock:
            await self._respect_cooldown()
            await self._close_if_idle_inner()
            context = await self._ensure_context_inner()
            if self._page is None or self._page.is_closed():
                self._page = await context.new_page()
            self.touch()
            return self._page

    async def rotate_and_restart(self) -> Page:
        """Close context, advance proxy and return a fresh legacy page."""
        async with self._lock:
            await self._respect_cooldown()
            await self._close_inner()
            self._advance_proxy()
            context = await self._ensure_context_inner()
            self._page = await context.new_page()
            self.touch()
            return self._page

    async def goto_and_wait(self, page: Page, url: str) -> None:
        """Navigate and wait for Wildberries' headed antibot challenge."""
        validate_main_frame_url('wildberries', url)
        await page.goto(url, wait_until='domcontentloaded', timeout=45_000)
        validate_main_frame_url('wildberries', page.url)
        deadline = time.monotonic() + settings.wb_challenge_timeout_sec
        while time.monotonic() < deadline:
            try:
                title = await page.title()
            except PlaywrightError:
                await page.wait_for_timeout(300)
                continue
            if title not in _PLACEHOLDER_TITLES:
                return
            await page.wait_for_timeout(500)
        raise BlockedError('WB antibot challenge did not resolve')

    def note_block(self) -> None:
        self._consecutive_blocks += 1
        max_blocks = settings.wb_max_consecutive_blocks
        if self._consecutive_blocks >= max_blocks:
            cooldown = settings.wb_block_cooldown_sec
            self._cooldown_until = time.monotonic() + cooldown
            logger.warning(
                'WB circuit open after %s blocks; cooldown %ss',
                self._consecutive_blocks,
                cooldown,
            )
            self._consecutive_blocks = 0

    def note_success(self) -> None:
        self._consecutive_blocks = 0
        self._cooldown_until = 0.0

    def touch(self) -> None:
        """Record use without closing the persistent context."""
        self._last_used_monotonic = time.monotonic()

    async def close_if_idle(self) -> None:
        """Close the browser after its configured idle interval."""
        async with self._lock:
            await self._close_if_idle_inner()

    async def close(self) -> None:
        async with self._lock:
            await self._close_inner()

    async def _respect_cooldown(self) -> None:
        remaining = self._cooldown_until - time.monotonic()
        if remaining <= 0:
            return
        raise BlockedError(
            f'WB circuit open for {remaining:.0f}s more (anti-bot cooldown)',
        )

    async def _ensure_context_inner(self) -> BrowserContext:
        if self._profile_lock is not None and (
            self._context is None
            or self._context_closed
            or self._playwright_stopped
        ):
            await self._close_inner()
        if self._context is None:
            await self._start_fresh()
        assert self._context is not None
        self.touch()
        return self._context

    async def _start_fresh(self) -> None:
        validate_single_browser_worker()
        if settings.wb_proxy_required and not settings.proxies:
            raise BlockedError(
                'WB_PROXY_REQUIRED=true but PROXY_LIST is empty',
            )
        proxy = self._pick_proxy()
        logger.info(
            'Starting WB browser session (proxy=%s)',
            'yes' if proxy else 'no',
        )
        self._current_proxy = proxy
        self._profile_lock = ProfileLock(self._profile_dir)
        self._profile_lock.acquire()
        try:
            self._playwright = await self._playwright_factory().start()
            self._playwright_stopped = False
            launch_kwargs: dict[str, Any] = {
                'user_data_dir': str(self._profile_dir),
                'headless': False,
                'args': chromium_runtime_args(),
                'locale': 'ru-RU',
                'timezone_id': 'Europe/Moscow',
                'viewport': {'width': 1440, 'height': 900},
                'user_agent': WB_DESKTOP_UA,
                'extra_http_headers': {
                    'Accept-Language': (
                        'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                    ),
                },
            }
            if proxy:
                launch_kwargs['proxy'] = playwright_proxy_config(proxy)
            self._context = (
                await self._playwright.chromium.launch_persistent_context(
                    **launch_kwargs,
                )
            )
            self._context_closed = False
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
            await _close_startup_pages(self._context)
            self.touch()
        except PlaywrightError as exc:
            await self._close_inner()
            if 'socks5 proxy authentication' in str(exc).lower():
                raise BlockedError(
                    'Chromium does not support authenticated SOCKS5 proxies',
                ) from exc
            raise
        except BaseException:
            await self._close_inner()
            raise

    def _pick_proxy(self) -> str | None:
        proxies = settings.proxies
        if not proxies:
            return None
        if self._current_proxy and self._current_proxy in proxies:
            return self._current_proxy
        if self._proxy_cycle is None:
            self._proxy_cycle = itertools.cycle(proxies)
        return next(self._proxy_cycle)

    def _advance_proxy(self) -> None:
        proxies = settings.proxies
        if len(proxies) <= 1:
            self._current_proxy = proxies[0] if proxies else None
            return
        if self._proxy_cycle is None:
            self._proxy_cycle = itertools.cycle(proxies)
        self._current_proxy = next(self._proxy_cycle)
        logger.info('Rotating WB proxy')

    async def _close_if_idle_inner(self) -> None:
        if self._context is None:
            return
        idle_sec = time.monotonic() - self._last_used_monotonic
        if idle_sec < self._idle_sec:
            return
        logger.info('Closing idle WB browser session')
        await self._close_inner()

    async def _close_inner(self) -> None:
        context = self._context
        playwright = self._playwright
        profile_lock = self._profile_lock
        if context is not None and not self._context_closed:
            await context.close()
            self._context_closed = True
            self._page = None
            self._context = None
        if playwright is not None and not self._playwright_stopped:
            await playwright.stop()
            self._playwright_stopped = True
            self._playwright = None
        if profile_lock is not None:
            profile_lock.release()
        self._profile_lock = None


async def _close_startup_pages(context: BrowserContext) -> None:
    for page in tuple(context.pages):
        if not page.is_closed():
            await page.close()
