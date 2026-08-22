"""Persistent Playwright context for Yandex Market browser fallback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.browser.profiles import ProfileLock, validate_single_browser_worker
from src.core.browser_proxy import STEALTH_INIT_SCRIPT, chromium_runtime_args
from src.core.config import settings


_DEFAULT_IDLE_SEC = 600
_YANDEX_DESKTOP_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


class YandexMarketBrowserSession:
    """Headed Playwright persistent Chromium session for Yandex Market."""

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        playwright_factory: Callable[[], Any] | None = None,
        idle_sec: int = _DEFAULT_IDLE_SEC,
    ) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._context_closed = True
        self._playwright_stopped = True
        self._profile_dir = profile_dir or settings.profile_dir(
            settings.runtime_role,
            'yandex_market',
        )
        self._playwright_factory = playwright_factory or async_playwright
        self._idle_sec = idle_sec
        self._profile_lock: ProfileLock | None = None
        self._last_used_monotonic = 0.0

    async def ensure_context(self) -> BrowserContext:
        """Return the persistent context, opening it lazily."""
        async with self._lock:
            await self._close_if_idle_inner()
            await self._finish_pending_close()
            if self._context is None:
                await self._start_fresh()
            assert self._context is not None
            self.touch()
            return self._context

    async def ensure_page(self) -> Page:
        """Return a compatibility page for direct callers."""
        async with self._lock:
            await self._close_if_idle_inner()
            await self._finish_pending_close()
            if self._context is None:
                await self._start_fresh()
            assert self._context is not None
            if self._page is None or self._page.is_closed():
                self._page = await self._context.new_page()
            self.touch()
            return self._page

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

    async def _start_fresh(self) -> None:
        validate_single_browser_worker()
        self._profile_lock = ProfileLock(self._profile_dir)
        self._profile_lock.acquire()
        try:
            self._playwright = await self._playwright_factory().start()
            self._playwright_stopped = False
            self._context = (
                await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    headless=False,
                    args=chromium_runtime_args(),
                    locale='ru-RU',
                    timezone_id='Europe/Moscow',
                    viewport={'width': 1440, 'height': 900},
                    user_agent=_YANDEX_DESKTOP_UA,
                    extra_http_headers={
                        'Accept-Language': (
                            'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
                        ),
                    },
                )
            )
            self._context_closed = False
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
            for page in tuple(self._context.pages):
                if not page.is_closed():
                    await page.close()
            self.touch()
        except BaseException:
            await self._close_inner()
            raise

    async def _close_if_idle_inner(self) -> None:
        if self._context is None:
            return
        idle_sec = time.monotonic() - self._last_used_monotonic
        if idle_sec < self._idle_sec:
            return
        await self._close_inner()

    async def _finish_pending_close(self) -> None:
        if self._profile_lock is not None and (
            self._context is None
            or self._context_closed
            or self._playwright_stopped
        ):
            await self._close_inner()

    async def _close_inner(self) -> None:
        context = self._context
        playwright = self._playwright
        profile_lock = self._profile_lock
        errors: list[BaseException] = []
        if context is not None and not self._context_closed:
            try:
                await context.close()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._context_closed = True
        if playwright is not None and not self._playwright_stopped:
            try:
                await playwright.stop()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._playwright_stopped = True
        if errors:
            raise errors[0]
        self._page = None
        self._context = None
        self._playwright = None
        self._profile_lock = None
        if profile_lock is not None:
            profile_lock.release()
