"""Persistent profile locks and marketplace-scoped browser leases."""

from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from src.browser.allowlist import (
    UnsafeMarketplaceUrl,
    validate_main_frame_url,
)
from src.browser.contracts import BrowserContextLike, PageLike
from src.marketplaces.contracts import MarketplaceName

if TYPE_CHECKING:
    from src.core.config import RuntimeRole, Settings


class ProfileInUseError(RuntimeError):
    """Another process owns the selected persistent browser profile."""


class BrowserProcessIsolationError(RuntimeError):
    """The process worker count cannot safely own browser profiles."""


class BrowserSessionCloseError(RuntimeError):
    """One or more persistent browser sessions could not close safely."""


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
        self._closing = False
        self._closed_sessions: set[MarketplaceName] = set()
        self._close_state_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Validate process isolation at application lifecycle startup."""
        if self._closed or self._closing:
            raise RuntimeError('browser session manager is closed or closing')
        validate_single_browser_worker()

    @asynccontextmanager
    async def lease(
        self,
        marketplace: MarketplaceName,
    ) -> AsyncIterator[PageLike]:
        """Yield one guarded task page for the whole marketplace operation."""
        if self._closed or self._closing:
            raise RuntimeError('browser session manager is closed or closing')
        try:
            session = self._sessions[marketplace]
            operation_lock = self._locks[marketplace]
        except KeyError as exc:
            raise ValueError('unsupported marketplace') from exc
        async with operation_lock:
            if self._closed or self._closing:
                raise RuntimeError(
                    'browser session manager is closed or closing',
                )
            validate_single_browser_worker()
            await session.close_if_idle()
            context = await session.ensure_context()
            page = await _open_page_with_recovery(session, context)
            page_guard = _LeasePageGuard(page, marketplace)
            body_failed = False
            try:
                yield page
            except BaseException:
                body_failed = True
                raise
            finally:
                close_error: BaseException | None = None
                try:
                    if not page.is_closed():
                        await page.close()
                except BaseException as exc:
                    close_error = exc
                finally:
                    try:
                        await page_guard.drain()
                    finally:
                        session.touch()
                if not body_failed:
                    if page_guard.unsafe_redirect is not None:
                        raise page_guard.unsafe_redirect
                    if close_error is not None:
                        raise close_error

    async def close(self) -> None:
        """Close every session once and share cleanup across callers."""
        async with self._close_state_lock:
            if self._closed:
                return
            self._closing = True
            if self._close_task is None:
                self._close_task = asyncio.create_task(
                    self._close_unresolved_sessions(),
                )
            close_task = self._close_task
        try:
            await asyncio.shield(close_task)
        finally:
            if close_task.done():
                async with self._close_state_lock:
                    if self._close_task is close_task:
                        self._close_task = None

    async def _close_unresolved_sessions(self) -> None:
        unresolved = tuple(
            (marketplace, session)
            for marketplace, session in self._sessions.items()
            if marketplace not in self._closed_sessions
        )
        results = await asyncio.gather(
            *(
                self._close_session(marketplace, session)
                for marketplace, session in unresolved
            ),
            return_exceptions=True,
        )
        failed = False
        for (marketplace, _), result in zip(unresolved, results):
            if isinstance(result, BaseException):
                failed = True
            else:
                self._closed_sessions.add(marketplace)
        if failed:
            raise BrowserSessionCloseError(
                'one or more browser sessions could not close safely',
            ) from None
        self._closed = True

    async def _close_session(
        self,
        marketplace: MarketplaceName,
        session: PersistentBrowserSession,
    ) -> None:
        async with self._locks[marketplace]:
            await session.close()


async def _open_page_with_recovery(
    session: PersistentBrowserSession,
    context: BrowserContextLike,
) -> PageLike:
    """Open a page, self-healing once if the cached context died externally.

    A session's own bookkeeping only learns a context is gone when the
    session itself closes it. If the real browser process behind that
    cached context dies for an external reason between two leases (crash,
    kill, or any other out-of-band closure), ``new_page()`` is the first
    call to notice: it raises. Treat that as proof the cached context is
    unusable, force the session to discard it, and retry exactly once
    against a genuinely fresh context. A second failure is a real problem
    and propagates unchanged to the existing transport-error handling.
    """
    try:
        return await context.new_page()
    except Exception:
        try:
            await session.close()
        except Exception:
            pass
        context = await session.ensure_context()
        return await context.new_page()


class _LeasePageGuard:
    """Track event cleanup so a lease cannot outlive browser side effects."""

    def __init__(
        self,
        page: PageLike,
        marketplace: MarketplaceName,
    ) -> None:
        self._page = page
        self._marketplace = marketplace
        self._tasks: set[asyncio.Task[None]] = set()
        self.unsafe_redirect: UnsafeMarketplaceUrl | None = None
        page.on('popup', self._close_popup)
        page.on('framenavigated', self._validate_redirect)

    def _close_popup(self, popup: PageLike) -> None:
        self._schedule_close(popup)

    def _validate_redirect(self, frame: object) -> None:
        if frame is not self._page.main_frame:
            return
        url = getattr(frame, 'url', '')
        try:
            validate_main_frame_url(self._marketplace, url)
        except UnsafeMarketplaceUrl as exc:
            self.unsafe_redirect = exc
            self._schedule_close(self._page)

    def _schedule_close(self, page: PageLike) -> None:
        task = asyncio.create_task(_close_page(page))
        self._tasks.add(task)
        task.add_done_callback(_consume_task_exception)

    async def drain(self) -> None:
        """Wait for every event cleanup scheduled during this lease."""
        while self._tasks:
            tasks = tuple(self._tasks)
            self._tasks.difference_update(tasks)
            await asyncio.gather(*tasks, return_exceptions=True)


async def _close_page(page: PageLike) -> None:
    if not page.is_closed():
        await page.close()


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


def build_sessions(
    role: RuntimeRole | None = None,
    settings: Settings | None = None,
) -> dict[MarketplaceName, PersistentBrowserSession]:
    """Build one persistent session per marketplace for a process role."""
    from src.browser.yandex_market import YandexMarketBrowserSession
    from src.core.config import settings as default_settings
    from src.ozon.session import OzonBrowserSession
    from src.wb.session import WBBrowserSession

    active = default_settings if settings is None else settings
    profile_role = active.runtime_role if role is None else role
    return {
        'ozon': OzonBrowserSession(
            profile_dir=active.profile_dir(profile_role, 'ozon'),
        ),
        'wildberries': WBBrowserSession(
            profile_dir=active.profile_dir(profile_role, 'wildberries'),
        ),
        'yandex_market': YandexMarketBrowserSession(
            profile_dir=active.profile_dir(profile_role, 'yandex_market'),
        ),
    }


def _default_sessions() -> dict[
    MarketplaceName,
    PersistentBrowserSession,
]:
    return build_sessions()
