from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.browser.profiles import BrowserSessionManager
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

    async def dispatch(self, event: str, value: Any) -> None:
        for handler in self.handlers.get(event, ()):
            result = handler(value)
            if inspect.isawaitable(result):
                await result


class FakeContext:
    def __init__(self, with_initial_page: bool = False) -> None:
        self.pages = [FakePage()] if with_initial_page else []
        self.created_pages: list[FakePage] = []
        self.closed = False
        self.init_scripts: list[str] = []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        self.created_pages.append(page)
        return page

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def close(self) -> None:
        self.closed = True
        for page in self.pages:
            await page.close()


class FakeSession:
    def __init__(self) -> None:
        self.context = FakeContext()
        self.close_if_idle_calls = 0
        self.touch_calls = 0
        self.close_calls = 0

    async def ensure_context(self) -> FakeContext:
        return self.context

    async def close_if_idle(self) -> None:
        self.close_if_idle_calls += 1

    def touch(self) -> None:
        self.touch_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
        await self.context.close()


class BrowserSessionManagerTests(unittest.IsolatedAsyncioTestCase):
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
            await page.dispatch('popup', popup)

            self.assertTrue(popup.closed)

    async def test_unsafe_main_frame_redirect_closes_page(self) -> None:
        manager = BrowserSessionManager({'ozon': FakeSession()})

        async with manager.lease('ozon') as page:
            page.main_frame.url = 'https://attacker.invalid/redirect'
            await page.dispatch('framenavigated', page.main_frame)

            self.assertTrue(page.closed)


class FakeChromium:
    def __init__(self) -> None:
        self.context = FakeContext(with_initial_page=True)
        self.launch_kwargs: dict[str, Any] | None = None

    async def launch_persistent_context(
        self,
        **kwargs: Any,
    ) -> FakeContext:
        self.launch_kwargs = kwargs
        return self.context


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakePlaywrightStarter:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


def fake_playwright_factory(playwright: FakePlaywright) -> Callable[[], Any]:
    return lambda: FakePlaywrightStarter(playwright)


class PersistentMarketplaceSessionTests(unittest.IsolatedAsyncioTestCase):
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


async def _no_op_warmup(page: FakePage) -> None:
    del page


if __name__ == '__main__':
    unittest.main()
