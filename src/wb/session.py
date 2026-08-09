from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from src.core.browser_proxy import STEALTH_INIT_SCRIPT, playwright_proxy_config
from src.core.config import settings
from src.parsers.utils import BlockedError
from src.wb.constants import WB_DESKTOP_UA

logger = logging.getLogger(__name__)

# WB's own antibot challenge (`/__wbaas/challenges/antibot/...`) reliably
# stalls forever under *any* headless Chromium mode (plain, --headless=new,
# with or without stealth patches) even from a clean residential IP — but
# resolves in a couple of seconds under a real headed session. So this
# session always launches headed; on a display-less server that means
# Chromium must run under Xvfb (see Dockerfile.bot).
_PLACEHOLDER_TITLES = ('', '...')


class WBBrowserSession:
    """Headed Playwright Chromium session with antibot wait + proxy rotation."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_used_monotonic = 0.0
        self._proxy_cycle: itertools.cycle[str] | None = None
        self._current_proxy: str | None = None
        self._consecutive_blocks = 0
        self._cooldown_until = 0.0

    async def ensure_page(self) -> Page:
        async with self._lock:
            await self._respect_cooldown()
            await self._close_if_idle()
            if self._page is not None and not self._page.is_closed():
                self._last_used_monotonic = time.monotonic()
                return self._page
            await self._start_fresh()
            assert self._page is not None
            return self._page

    async def rotate_and_restart(self) -> Page:
        async with self._lock:
            await self._respect_cooldown()
            await self._close_inner()
            self._advance_proxy()
            await self._start_fresh()
            assert self._page is not None
            return self._page

    async def goto_and_wait(self, page: Page, url: str) -> None:
        """Navigate and wait out WB's antibot challenge (headed sessions
        usually clear it in 2-8s; a cold, never-warmed IP can take longer)."""
        await page.goto(url, wait_until='domcontentloaded', timeout=45_000)
        deadline = time.monotonic() + settings.wb_challenge_timeout_sec
        while time.monotonic() < deadline:
            try:
                title = await page.title()
            except PlaywrightError:
                # Mid-navigation (the challenge JS reloads the page) — retry.
                await page.wait_for_timeout(300)
                continue
            if title not in _PLACEHOLDER_TITLES:
                return
            await page.wait_for_timeout(500)

        raise BlockedError(f'WB antibot challenge did not resolve for {url}')

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

    async def close(self) -> None:
        async with self._lock:
            await self._close_inner()

    async def _respect_cooldown(self) -> None:
        remaining = self._cooldown_until - time.monotonic()
        if remaining <= 0:
            return
        raise BlockedError(
            f'WB circuit open for {remaining:.0f}s more (anti-bot cooldown)'
        )

    async def _start_fresh(self) -> None:
        if settings.wb_proxy_required and not settings.proxies:
            raise BlockedError('WB_PROXY_REQUIRED=true but PROXY_LIST is empty')

        logger.info(
            'Starting WB browser session (proxy=%s)',
            'yes' if self._pick_proxy() else 'no',
        )
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            'headless': False,
            'args': [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
            ],
        }
        proxy = self._pick_proxy()
        self._current_proxy = proxy
        if proxy:
            launch_kwargs['proxy'] = playwright_proxy_config(proxy)

        try:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        except PlaywrightError as exc:
            if 'socks5 proxy authentication' in str(exc).lower():
                raise BlockedError(
                    'Chromium does not support authenticated SOCKS5 proxies '
                    '(PROXY_LIST). Use an HTTP(S) proxy with auth, or an '
                    'unauthenticated (IP-whitelisted) SOCKS5 proxy instead.'
                ) from exc
            raise
        self._context = await self._browser.new_context(
            locale='ru-RU',
            timezone_id='Europe/Moscow',
            viewport={'width': 1440, 'height': 900},
            user_agent=WB_DESKTOP_UA,
            extra_http_headers={
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            },
        )
        await self._context.add_init_script(STEALTH_INIT_SCRIPT)
        self._page = await self._context.new_page()
        self._last_used_monotonic = time.monotonic()

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

    async def _close_if_idle(self) -> None:
        if self._page is None:
            return
        idle_sec = time.monotonic() - self._last_used_monotonic
        if idle_sec < settings.wb_browser_idle_sec:
            return
        logger.info('Closing idle WB browser session')
        await self._close_inner()

    async def _close_inner(self) -> None:
        if self._page is not None and not self._page.is_closed():
            await self._page.close()
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
