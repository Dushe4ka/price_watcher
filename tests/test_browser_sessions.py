from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import src.browser.profiles as browser_profiles
from src.browser.allowlist import UnsafeMarketplaceUrl
from src.browser.profiles import (
    BrowserProcessIsolationError,
    BrowserSessionManager,
    ProfileInUseError,
    ProfileLock,
)
from src.browser.yandex_market import YandexMarketBrowserSession
from src.ozon.session import OzonBrowserSession
from src.wb.session import WBBrowserSession


class FakeFrame:
    def __init__(self, url: str) -> None:
        self.url = url


class FakePage:
    def __init__(self) -> None:
        self.url = 'about:blank'
        self.main_frame = FakeFrame(self.url)
        self.closed = False
        self.handlers: dict[str, list[Callable[[Any], Any]]] = {}

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    def on(self, event: str, handler: Callable[[Any], Any]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def dispatch(self, event: str, value: Any) -> None:
        for handler in self.handlers.get(event, ()):
            result = handler(value)
            if inspect.isawaitable(result):
                asyncio.create_task(result)


class DelayedClosePage(FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        await super().close()


class FakeContext:
    def __init__(
        self,
        with_initial_page: bool = False,
        close_failures: int = 0,
    ) -> None:
        self.pages = [FakePage()] if with_initial_page else []
        self.created_pages: list[FakePage] = []
        self.closed = False
        self.init_scripts: list[str] = []
        self.close_failures = close_failures
        self.close_calls = 0

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        self.created_pages.append(page)
        return page

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError('synthetic context close failure')
        self.closed = True
        for page in self.pages:
            await page.close()


class FakeSession:
    def __init__(
        self,
        *,
        close_failures: int = 0,
        close_release: asyncio.Event | None = None,
    ) -> None:
        self.context = FakeContext()
        self.close_if_idle_calls = 0
        self.ensure_context_calls = 0
        self.touch_calls = 0
        self.close_calls = 0
        self.close_failures = close_failures
        self.close_started = asyncio.Event()
        self.close_release = close_release

    async def ensure_context(self) -> FakeContext:
        self.ensure_context_calls += 1
        return self.context

    async def close_if_idle(self) -> None:
        self.close_if_idle_calls += 1

    def touch(self) -> None:
        self.touch_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError('synthetic session close failure')
        await self.context.close()


class BrowserSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._environment = patch.dict(
            os.environ,
            {'WEB_CONCURRENCY': '1'},
        )
        self._environment.start()
        self.addCleanup(self._environment.stop)

    async def test_same_marketplace_leases_are_serialized(self) -> None:
        session = FakeSession()
        manager = BrowserSessionManager({'ozon': session})
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        observed: list[str] = []

        async def first() -> None:
            async with manager.lease('ozon'):
                observed.append('first-enter')
                first_entered.set()
                await release_first.wait()
                observed.append('first-exit')

        async def second() -> None:
            await first_entered.wait()
            async with manager.lease('ozon'):
                observed.append('second-enter')

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await first_entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(['first-enter'], observed)
        release_first.set()
        await asyncio.gather(first_task, second_task)

        self.assertEqual(
            ['first-enter', 'first-exit', 'second-enter'],
            observed,
        )

    async def test_different_marketplaces_can_run_concurrently(self) -> None:
        manager = BrowserSessionManager(
            {
                'ozon': FakeSession(),
                'wildberries': FakeSession(),
            },
        )
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            async with manager.lease('ozon'):
                first_entered.set()
                await release.wait()

        async def second() -> None:
            await first_entered.wait()
            async with manager.lease('wildberries'):
                second_entered.set()

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await asyncio.wait_for(second_entered.wait(), timeout=1)
        release.set()
        await asyncio.gather(first_task, second_task)

    async def test_lease_closes_page_but_keeps_context_alive(self) -> None:
        session = FakeSession()
        manager = BrowserSessionManager({'ozon': session})

        async with manager.lease('ozon') as first_page:
            self.assertFalse(first_page.closed)

        self.assertTrue(first_page.closed)
        self.assertFalse(session.context.closed)
        async with manager.lease('ozon') as second_page:
            self.assertIsNot(first_page, second_page)

        self.assertEqual(2, session.close_if_idle_calls)
        self.assertEqual(2, session.touch_calls)
        await manager.close()
        self.assertTrue(session.context.closed)
        self.assertEqual(1, session.close_calls)

    async def test_popup_is_closed(self) -> None:
        manager = BrowserSessionManager({'ozon': FakeSession()})

        async with manager.lease('ozon') as page:
            popup = FakePage()
            page.dispatch('popup', popup)

        self.assertTrue(popup.closed)

    async def test_lease_exit_waits_for_popup_close_task(self) -> None:
        manager = BrowserSessionManager({'ozon': FakeSession()})
        popup = DelayedClosePage()

        async def use_page() -> None:
            async with manager.lease('ozon') as page:
                page.dispatch('popup', popup)

        operation = asyncio.create_task(use_page())
        await asyncio.wait_for(popup.close_started.wait(), timeout=1)

        self.assertFalse(operation.done())
        popup.release_close.set()
        await operation
        self.assertTrue(popup.closed)

    async def test_unsafe_main_frame_redirect_fails_the_lease(self) -> None:
        manager = BrowserSessionManager({'ozon': FakeSession()})

        with self.assertRaises(UnsafeMarketplaceUrl):
            async with manager.lease('ozon') as page:
                page.main_frame.url = 'https://attacker.invalid/redirect'
                page.dispatch('framenavigated', page.main_frame)

        self.assertTrue(page.closed)

    async def test_unsafe_redirect_does_not_override_body_exception(
        self,
    ) -> None:
        manager = BrowserSessionManager({'ozon': FakeSession()})

        with self.assertRaisesRegex(RuntimeError, 'body failed'):
            async with manager.lease('ozon') as page:
                page.main_frame.url = 'https://attacker.invalid/redirect'
                page.dispatch('framenavigated', page.main_frame)
                raise RuntimeError('body failed')

        self.assertTrue(page.closed)

    async def test_start_rejects_missing_worker_count_before_launch(
        self,
    ) -> None:
        session = FakeSession()
        manager = BrowserSessionManager({'ozon': session})

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(BrowserProcessIsolationError):
                await manager.start()
            with self.assertRaises(BrowserProcessIsolationError):
                async with manager.lease('ozon'):
                    pass

        self.assertEqual(0, session.ensure_context_calls)

    async def test_close_attempts_all_sessions_and_retries_failures(
        self,
    ) -> None:
        failing = FakeSession(close_failures=1)
        successful = FakeSession()
        manager = BrowserSessionManager(
            {'ozon': failing, 'wildberries': successful},
        )

        with self.assertRaises(browser_profiles.BrowserSessionCloseError):
            await manager.close()

        self.assertEqual(1, failing.close_calls)
        self.assertEqual(1, successful.close_calls)
        await manager.close()
        self.assertEqual(2, failing.close_calls)
        self.assertEqual(1, successful.close_calls)

    async def test_concurrent_close_callers_wait_for_one_cleanup(
        self,
    ) -> None:
        release_close = asyncio.Event()
        session = FakeSession(close_release=release_close)
        manager = BrowserSessionManager({'ozon': session})

        first = asyncio.create_task(manager.close())
        await session.close_started.wait()
        second = asyncio.create_task(manager.close())
        await asyncio.sleep(0)

        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.assertEqual(1, session.close_calls)
        release_close.set()
        await asyncio.gather(first, second)
        await manager.close()
        self.assertEqual(1, session.close_calls)

    async def test_lease_waiting_behind_close_cannot_reopen_session(
        self,
    ) -> None:
        release_close = asyncio.Event()
        session = FakeSession(close_release=release_close)
        manager = BrowserSessionManager({'ozon': session})
        close_task = asyncio.create_task(manager.close())
        await session.close_started.wait()

        async def try_lease() -> None:
            async with manager.lease('ozon'):
                pass

        lease_task = asyncio.create_task(try_lease())
        await asyncio.sleep(0)
        release_close.set()
        await close_task

        with self.assertRaisesRegex(RuntimeError, 'closed'):
            await lease_task
        self.assertEqual(0, session.ensure_context_calls)


class FakeChromium:
    def __init__(self, context: FakeContext | None = None) -> None:
        self.context = context or FakeContext(with_initial_page=True)
        self.launch_kwargs: dict[str, Any] | None = None

    async def launch_persistent_context(
        self,
        **kwargs: Any,
    ) -> FakeContext:
        self.launch_kwargs = kwargs
        return self.context


class FakePlaywright:
    def __init__(
        self,
        *,
        context: FakeContext | None = None,
        stop_failures: int = 0,
    ) -> None:
        self.chromium = FakeChromium(context)
        self.stopped = False
        self.stop_failures = stop_failures
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError('synthetic driver stop failure')
        self.stopped = True


class FakePlaywrightStarter:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


def fake_playwright_factory(playwright: FakePlaywright) -> Callable[[], Any]:
    return lambda: FakePlaywrightStarter(playwright)


class PersistentMarketplaceSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._environment = patch.dict(
            os.environ,
            {'WEB_CONCURRENCY': '1'},
        )
        self._environment.start()
        self.addCleanup(self._environment.stop)

    async def test_ozon_uses_headed_patchright_chrome_profile(self) -> None:
        playwright = FakePlaywright()
        with tempfile.TemporaryDirectory() as temporary_directory:
            session = OzonBrowserSession(
                profile_dir=Path(temporary_directory, 'api', 'ozon'),
                playwright_factory=fake_playwright_factory(playwright),
            )
            session._warm_antibot = _no_op_warmup
            context = await session.ensure_context()
            kwargs = playwright.chromium.launch_kwargs
            await session.close()

        self.assertIs(playwright.chromium.context, context)
        self.assertIsNotNone(kwargs)
        self.assertFalse(kwargs['headless'])
        self.assertEqual('chrome', kwargs['channel'])
        self.assertNotIn('--no-sandbox', kwargs['args'])
        self.assertNotIn('--disable-dev-shm-usage', kwargs['args'])
        self.assertTrue(playwright.stopped)

    async def test_wb_uses_headed_playwright_persistent_context(self) -> None:
        playwright = FakePlaywright()
        with tempfile.TemporaryDirectory() as temporary_directory:
            session = WBBrowserSession(
                profile_dir=Path(
                    temporary_directory,
                    'bot',
                    'wildberries',
                ),
                playwright_factory=fake_playwright_factory(playwright),
            )
            context = await session.ensure_context()
            kwargs = playwright.chromium.launch_kwargs
            await session.close()

        self.assertIs(playwright.chromium.context, context)
        self.assertIsNotNone(kwargs)
        self.assertFalse(kwargs['headless'])
        self.assertNotIn('--no-sandbox', kwargs['args'])
        self.assertNotIn('--disable-dev-shm-usage', kwargs['args'])
        self.assertTrue(playwright.stopped)

    async def test_yandex_uses_its_own_headed_persistent_context(self) -> None:
        playwright = FakePlaywright()
        with tempfile.TemporaryDirectory() as temporary_directory:
            session = YandexMarketBrowserSession(
                profile_dir=Path(
                    temporary_directory,
                    'local',
                    'yandex_market',
                ),
                playwright_factory=fake_playwright_factory(playwright),
            )
            context = await session.ensure_context()
            kwargs = playwright.chromium.launch_kwargs
            await session.close()

        self.assertIs(playwright.chromium.context, context)
        self.assertIsNotNone(kwargs)
        self.assertFalse(kwargs['headless'])
        self.assertNotIn('--no-sandbox', kwargs['args'])
        self.assertNotIn('--disable-dev-shm-usage', kwargs['args'])
        self.assertTrue(playwright.stopped)

    async def test_close_failure_keeps_each_marketplace_profile_locked(
        self,
    ) -> None:
        cases = (
            (OzonBrowserSession, 'api', 'ozon'),
            (WBBrowserSession, 'bot', 'wildberries'),
            (YandexMarketBrowserSession, 'local', 'yandex_market'),
        )

        for session_type, role, marketplace in cases:
            with self.subTest(marketplace=marketplace):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    profile_dir = Path(
                        temporary_directory,
                        role,
                        marketplace,
                    )
                    context = FakeContext(
                        with_initial_page=True,
                        close_failures=1,
                    )
                    playwright = FakePlaywright(context=context)
                    session = session_type(
                        profile_dir=profile_dir,
                        playwright_factory=(
                            fake_playwright_factory(playwright)
                        ),
                    )
                    if isinstance(session, OzonBrowserSession):
                        session._warm_antibot = _no_op_warmup
                    await session.ensure_context()

                    with self.assertRaisesRegex(
                        RuntimeError,
                        'context close failure',
                    ):
                        await session.close()
                    competing_lock = ProfileLock(profile_dir)
                    with self.assertRaises(ProfileInUseError):
                        competing_lock.acquire()

                    await session.close()
                    competing_lock.acquire()
                    competing_lock.release()
                    self.assertEqual(2, context.close_calls)
                    self.assertEqual(1, playwright.stop_calls)

    async def test_driver_stop_failure_keeps_profile_locked(self) -> None:
        context = FakeContext(with_initial_page=True)
        playwright = FakePlaywright(
            context=context,
            stop_failures=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            profile_dir = Path(
                temporary_directory,
                'local',
                'yandex_market',
            )
            session = YandexMarketBrowserSession(
                profile_dir=profile_dir,
                playwright_factory=fake_playwright_factory(playwright),
            )
            await session.ensure_context()

            with self.assertRaisesRegex(RuntimeError, 'driver stop failure'):
                await session.close()
            competing_lock = ProfileLock(profile_dir)
            with self.assertRaises(ProfileInUseError):
                competing_lock.acquire()

            await session.close()
            competing_lock.acquire()
            competing_lock.release()

        self.assertEqual(1, context.close_calls)
        self.assertEqual(2, playwright.stop_calls)


async def _no_op_warmup(page: FakePage) -> None:
    del page


if __name__ == '__main__':
    unittest.main()
