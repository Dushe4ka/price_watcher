from __future__ import annotations

import asyncio
import itertools
import logging
import time
from pathlib import Path
from typing import Any

from patchright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    async_playwright,
)

from src.core.browser_proxy import STEALTH_INIT_SCRIPT, playwright_proxy_config
from src.core.config import settings
from src.ozon.constants import (
    OZON_CHALLENGE_TITLE_MARKERS,
    OZON_DESKTOP_UA,
    OZON_HOME_URL,
)
from src.parsers.utils import BlockedError

logger = logging.getLogger(__name__)


class OzonBrowserSession:
    """Patchright (undetected Playwright fork) Chrome session with antibot
    warmup and proxy rotation.

    Plain Playwright's CDP fingerprint (Runtime.enable leak,
    --enable-automation, etc.) gets flagged by Ozon's antibot even from a
    clean private mobile IP in both headless and headed mode — patchright
    patches those CDP-level tells directly. Headed + the real "chrome"
    channel (not bundled Chromium) is patchright's own recommended stealth
    configuration, so this session no longer offers a headless mode.

    Uses ``launch_persistent_context`` against a durable profile directory
    (``OZON_PROFILE_DIR``, mounted as a Docker volume in prod) rather than a
    fresh throwaway profile per run — patchright's docs list a
    real/aged profile as the top stealth lever, above the "chrome" channel
    alone. A brand-new run still looks like a brand-new browser; the payoff
    is cumulative across real runs over time.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
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
        """Close browser, advance proxy, open a fresh warmed session."""
        async with self._lock:
            await self._respect_cooldown()
            await self._close_inner()
            self._advance_proxy()
            await self._start_fresh()
            assert self._page is not None
            return self._page

    def note_block(self) -> None:
        self._consecutive_blocks += 1
        max_blocks = settings.ozon_max_consecutive_blocks
        if self._consecutive_blocks >= max_blocks:
            cooldown = settings.ozon_block_cooldown_sec
            self._cooldown_until = time.monotonic() + cooldown
            logger.warning(
                'Ozon circuit open after %s blocks; cooldown %ss',
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
            f'Ozon circuit open for {remaining:.0f}s more '
            f'(anti-bot cooldown)'
        )

    async def _start_fresh(self) -> None:
        if settings.ozon_proxy_required and not settings.proxies:
            raise BlockedError(
                'OZON_PROXY_REQUIRED=true but PROXY_LIST is empty'
            )

        logger.info(
            'Starting Ozon browser session (proxy=%s)',
            'yes' if self._pick_proxy() else 'no',
        )
        self._playwright = await async_playwright().start()
        profile_dir = Path(settings.ozon_profile_dir).expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)

        launch_kwargs: dict[str, Any] = {
            'user_data_dir': str(profile_dir),
            'headless': False,
            'channel': 'chrome',
            'no_viewport': True,
            'locale': 'ru-RU',
            'timezone_id': 'Europe/Moscow',
            'user_agent': OZON_DESKTOP_UA,
            'extra_http_headers': {
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            },
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
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs,
            )
        except PlaywrightError as exc:
            if 'socks5 proxy authentication' in str(exc).lower():
                raise BlockedError(
                    'Chromium does not support authenticated SOCKS5 proxies '
                    '(PROXY_LIST). Use an HTTP(S) proxy with auth, or an '
                    'unauthenticated (IP-whitelisted) SOCKS5 proxy instead.'
                ) from exc
            raise
        await self._context.add_init_script(STEALTH_INIT_SCRIPT)
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        await self._warm_antibot(self._page)
        self._last_used_monotonic = time.monotonic()

    async def _warm_antibot(self, page: Page) -> None:
        await page.goto(OZON_HOME_URL, wait_until='domcontentloaded', timeout=60_000)
        deadline = time.monotonic() + settings.ozon_challenge_timeout_sec
        while time.monotonic() < deadline:
            if await self._challenge_passed(page):
                logger.info('Ozon antibot challenge passed (%s)', page.url)
                return
            if await self._is_hard_fail_page(page):
                try:
                    btn = page.get_by_text('Обновить страницу')
                    if await btn.count():
                        await btn.first.click(timeout=2000)
                        await page.wait_for_timeout(1500)
                        continue
                except Exception:
                    pass
                await page.reload(wait_until='domcontentloaded')
            await page.wait_for_timeout(500)

        title = await page.title()
        raise BlockedError(
            f'Ozon antibot challenge failed: title={title!r} url={page.url}'
        )

    async def _challenge_passed(self, page: Page) -> bool:
        try:
            title = (await page.title()).lower()
        except PlaywrightError:
            # Mid-navigation (challenge JS is reloading the page) — retry.
            return False
        if any(marker in title for marker in OZON_CHALLENGE_TITLE_MARKERS):
            return False
        url = page.url
        if 'abt_att=' in url:
            return True
        try:
            body = await page.inner_text('body')
        except Exception:
            return False
        if 'Инцидент' in body and 'нет' in body.lower():
            return False
        try:
            return 'Каталог' in body or 'OZON' in (await page.title()).upper()
        except PlaywrightError:
            return False

    async def _is_hard_fail_page(self, page: Page) -> bool:
        try:
            title = (await page.title()).lower()
        except PlaywrightError:
            return False
        return any(
            marker in title
            for marker in ('нет соединения', 'нет\xa0соединения')
        )

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
        logger.info('Rotating Ozon proxy')

    async def _close_if_idle(self) -> None:
        if self._page is None:
            return
        idle_sec = time.monotonic() - self._last_used_monotonic
        if idle_sec < settings.ozon_browser_idle_sec:
            return
        logger.info('Closing idle Ozon browser session')
        await self._close_inner()

    async def _close_inner(self) -> None:
        # Closing a persistent context also tears down its browser process —
        # no separate Browser handle to close here.
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._playwright = None
