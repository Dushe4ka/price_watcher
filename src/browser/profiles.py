"""Persistent profile locks and marketplace-scoped browser leases."""

from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from src.browser.allowlist import (
    UnsafeMarketplaceUrl,
    validate_main_frame_url,
)
from src.browser.contracts import BrowserContextLike, PageLike
from src.marketplaces.contracts import MarketplaceName


class ProfileInUseError(RuntimeError):
    """Another process owns the selected persistent browser profile."""


class BrowserProcessIsolationError(RuntimeError):
    """The process worker count cannot safely own browser profiles."""


class ProfileLock:
    """Own a non-blocking OS lock for one persistent profile lifetime."""

    def __init__(self, profile_dir: Path) -> None:
        self.profile_dir = profile_dir.expanduser().resolve()
        self._descriptor: int | None = None

    def acquire(self) -> None:
        """Create the private profile and fail if another opener owns it."""
        if self._descriptor is not None:
            return
        self.profile_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.profile_dir, 0o700)
        lock_path = self.profile_dir / '.profile.lock'
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise ProfileInUseError(
                'persistent browser profile is already in use',
            ) from exc
        self._descriptor = descriptor

    def release(self) -> None:
        """Release this opener's OS lock without unlinking the lock file."""
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> ProfileLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def validate_single_browser_worker(
    environment: Mapping[str, str] | None = None,
) -> None:
    """Require one web worker before a persistent browser can start."""
    values = os.environ if environment is None else environment
    concurrency = values.get('WEB_CONCURRENCY')
    if concurrency is None:
        return
    if concurrency != '1':
        raise BrowserProcessIsolationError(
            'persistent browsers require WEB_CONCURRENCY=1',
        )


class PersistentBrowserSession(Protocol):
    """Lifecycle surface supplied by each marketplace session."""

    async def ensure_context(self) -> BrowserContextLike:
        """Return a live persistent context, opening it lazily."""

    async def close_if_idle(self) -> None:
        """Close the context when its configured idle limit elapsed."""

    def touch(self) -> None:
        """Record completion of a marketplace operation."""

    async def close(self) -> None:
        """Close the persistent context and underlying driver."""


class BrowserSessionManager:
    """Serialize one marketplace operation while preserving its context."""

    def __init__(
        self,
        sessions: Mapping[
            MarketplaceName,
            PersistentBrowserSession,
        ] | None = None,
    ) -> None:
        self._sessions = dict(sessions or _default_sessions())
        self._locks = {
            marketplace: asyncio.Lock()
            for marketplace in self._sessions
        }
        self._closed = False

    @asynccontextmanager
    async def lease(
        self,
        marketplace: MarketplaceName,
    ) -> AsyncIterator[PageLike]:
        """Yield one guarded task page for the whole marketplace operation."""
        if self._closed:
            raise RuntimeError('browser session manager is closed')
        try:
            session = self._sessions[marketplace]
            operation_lock = self._locks[marketplace]
        except KeyError as exc:
            raise ValueError('unsupported marketplace') from exc
        async with operation_lock:
            validate_single_browser_worker()
            await session.close_if_idle()
            context = await session.ensure_context()
            page = await context.new_page()
            _guard_task_page(page, marketplace)
            try:
                yield page
            finally:
                try:
                    if not page.is_closed():
                        await page.close()
                finally:
                    session.touch()

    async def close(self) -> None:
        """Close all contexts after any active marketplace leases finish."""
        if self._closed:
            return
        self._closed = True
        for marketplace, session in self._sessions.items():
            async with self._locks[marketplace]:
                await session.close()


def _guard_task_page(page: PageLike, marketplace: MarketplaceName) -> None:
    async def close_popup(popup: PageLike) -> None:
        if not popup.is_closed():
            await popup.close()

    async def validate_redirect(frame: object) -> None:
        if frame is not page.main_frame:
            return
        url = getattr(frame, 'url', '')
        try:
            validate_main_frame_url(marketplace, url)
        except UnsafeMarketplaceUrl:
            if not page.is_closed():
                await page.close()

    page.on('popup', close_popup)
    page.on('framenavigated', validate_redirect)


def _default_sessions() -> dict[
    MarketplaceName,
    PersistentBrowserSession,
]:
    from src.browser.yandex_market import YandexMarketBrowserSession
    from src.core.config import settings
    from src.ozon.session import OzonBrowserSession
    from src.wb.session import WBBrowserSession

    role = settings.runtime_role
    return {
        'ozon': OzonBrowserSession(
            profile_dir=settings.profile_dir(role, 'ozon'),
        ),
        'wildberries': WBBrowserSession(
            profile_dir=settings.profile_dir(role, 'wildberries'),
        ),
        'yandex_market': YandexMarketBrowserSession(
            profile_dir=settings.profile_dir(role, 'yandex_market'),
        ),
    }
