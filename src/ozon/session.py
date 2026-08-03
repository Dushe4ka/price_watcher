from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.core.config import settings

logger = logging.getLogger(__name__)


class OzonBrowserSession:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_used_monotonic = 0.0

    async def ensure_page(self) -> Page:
        async with self._lock:
            await self._close_if_idle()
            if self._page is not None and not self._page.is_closed():
                self._last_used_monotonic = time.monotonic()
                return self._page

            logger.info('Starting Ozon browser session')
            self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                'headless': True,
                'args': ['--no-sandbox', '--disable-dev-shm-usage'],
            }
            proxy = settings.proxies[0] if settings.proxies else None
            if proxy:
                launch_kwargs['proxy'] = {'server': proxy}
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
            await self._page.goto('https://www.ozon.ru/', wait_until='domcontentloaded')
            self._last_used_monotonic = time.monotonic()
            return self._page

    async def close(self) -> None:
        async with self._lock:
            await self._close_inner()

    async def _close_if_idle(self) -> None:
        if self._page is None:
            return
        idle_sec = time.monotonic() - self._last_used_monotonic
        if idle_sec < settings.ozon_browser_idle_sec:
            return
        logger.info('Closing idle Ozon browser session')
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
