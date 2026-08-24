"""API and bot shutdown release marketplace resources exactly once."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.browser.profiles import (
    BrowserProcessIsolationError,
    BrowserSessionManager,
    ProfileInUseError,
)
from src.marketplaces.contracts import SourceName
from src.marketplaces.registry import MarketplaceSourceRegistry
from src.marketplaces.service import (
    configure_marketplace_registry,
    configure_marketplace_runtime,
    marketplace_runtime_role,
)
from tests.browser_source_fakes import FakeCoordinator
from tests.marketplace_service_fakes import StubRegistry, StubSource
from tests.test_marketplace_settings import make_settings


class FakeScheduler:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self, wait: bool = False) -> None:
        del wait
        self.shutdown_calls += 1


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data: dict[str, object] = {}
        self.bot = object()


class IdleSession:
    """Session stub that never opens a context: no browser is ever started."""

    async def ensure_context(self) -> object:
        raise AssertionError('tests never open a real browser context')

    async def close_if_idle(self) -> None:
        return None

    def touch(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _stub_registry(*, start_error: Exception | None = None) -> StubRegistry:
    registry = StubRegistry(
        ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
        start_error=start_error,
    )
    configure_marketplace_registry(registry)
    return registry


def _isolation_registry() -> MarketplaceSourceRegistry:
    """Compose a real registry over a manager with no real sessions."""
    registry = MarketplaceSourceRegistry(
        settings=make_settings(),
        manager=BrowserSessionManager({'ozon': IdleSession()}),
        coordinator=FakeCoordinator(),
        category_urls={},
    )
    configure_marketplace_registry(registry)
    return registry


class ApiLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_marketplace_registry(None)

    def test_client_shutdown_closes_resources_exactly_once(self) -> None:
        from src import main

        registry = _stub_registry()

        with patch.object(
            main,
            'create_first_superuser',
            AsyncMock(),
        ):
            with TestClient(main.app):
                pass

        self.assertEqual(1, registry.close_calls)

    def test_repeated_lifespan_shutdown_stays_idempotent(self) -> None:
        from src import main

        registry = _stub_registry()

        with patch.object(
            main,
            'create_first_superuser',
            AsyncMock(),
        ):
            with TestClient(main.app):
                pass
            second = _stub_registry()
            with TestClient(main.app):
                pass

        self.assertEqual(1, registry.close_calls)
        self.assertEqual(1, second.close_calls)

    def test_startup_starts_marketplace_resources_once(self) -> None:
        from src import main

        registry = _stub_registry()

        with patch.object(
            main,
            'create_first_superuser',
            AsyncMock(),
        ):
            with TestClient(main.app):
                pass

        self.assertEqual(1, registry.start_calls)

    def test_startup_fails_when_worker_isolation_is_misconfigured(
        self,
    ) -> None:
        from src import main

        _isolation_registry()

        with patch.dict(os.environ, {'WEB_CONCURRENCY': '4'}):
            with patch.object(
                main,
                'create_first_superuser',
                AsyncMock(),
            ):
                with self.assertRaises(BrowserProcessIsolationError):
                    with TestClient(main.app):
                        pass

    def test_startup_succeeds_with_one_browser_worker(self) -> None:
        from src import main

        registry = _isolation_registry()

        with patch.dict(os.environ, {'WEB_CONCURRENCY': '1'}):
            with patch.object(
                main,
                'create_first_superuser',
                AsyncMock(),
            ):
                with TestClient(main.app) as client:
                    self.assertIsNotNone(client)

        with self.assertRaises(RuntimeError):
            registry.sources_for('ozon')

    def test_startup_fails_when_the_profile_is_already_locked(self) -> None:
        from src import main

        _stub_registry(
            start_error=ProfileInUseError('profile already in use'),
        )

        with patch.object(
            main,
            'create_first_superuser',
            AsyncMock(),
        ):
            with self.assertRaises(ProfileInUseError):
                with TestClient(main.app):
                    pass

    def test_startup_failure_precedes_superuser_creation(self) -> None:
        from src import main

        _stub_registry(
            start_error=ProfileInUseError('profile already in use'),
        )
        superuser = AsyncMock()

        with patch.object(main, 'create_first_superuser', superuser):
            with self.assertRaises(ProfileInUseError):
                with TestClient(main.app):
                    pass

        superuser.assert_not_awaited()

    def test_api_process_uses_the_api_runtime_role(self) -> None:
        from src import main

        configure_marketplace_runtime('local')
        _stub_registry()

        with patch.object(
            main,
            'create_first_superuser',
            AsyncMock(),
        ):
            with TestClient(main.app):
                role = marketplace_runtime_role()

        self.assertEqual('api', role)


class BotLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        configure_marketplace_registry(None)

    async def test_post_shutdown_closes_resources_exactly_once(self) -> None:
        from bot import deals_scheduler

        registry = _stub_registry()
        application = FakeApplication()
        scheduler = FakeScheduler()
        application.bot_data['deals_scheduler'] = scheduler

        await deals_scheduler.bot_post_shutdown(application)
        await deals_scheduler.bot_post_shutdown(application)

        self.assertEqual(1, registry.close_calls)
        self.assertEqual(1, scheduler.shutdown_calls)

    async def test_post_shutdown_without_scheduler_still_closes(self) -> None:
        from bot import deals_scheduler

        registry = _stub_registry()

        await deals_scheduler.bot_post_shutdown(FakeApplication())

        self.assertEqual(1, registry.close_calls)

    async def test_bot_process_uses_the_bot_runtime_role(self) -> None:
        from bot import deals_scheduler

        configure_marketplace_runtime('local')
        _stub_registry()

        with patch.object(
            deals_scheduler,
            'setup_bot_commands',
            AsyncMock(),
        ):
            with patch.object(
                deals_scheduler,
                'start_deals_scheduler',
                AsyncMock(),
            ):
                await deals_scheduler.bot_post_init(FakeApplication())

        self.assertEqual('bot', marketplace_runtime_role())

    async def test_post_init_starts_marketplace_resources(self) -> None:
        from bot import deals_scheduler

        registry = _stub_registry()

        with patch.object(
            deals_scheduler,
            'setup_bot_commands',
            AsyncMock(),
        ):
            with patch.object(
                deals_scheduler,
                'start_deals_scheduler',
                AsyncMock(),
            ):
                await deals_scheduler.bot_post_init(FakeApplication())

        self.assertEqual(1, registry.start_calls)

    async def test_post_init_fails_before_polling_on_bad_isolation(
        self,
    ) -> None:
        from bot import deals_scheduler

        _isolation_registry()
        commands = AsyncMock()
        scheduler = AsyncMock()

        with patch.dict(os.environ, {'WEB_CONCURRENCY': '4'}):
            with patch.object(deals_scheduler, 'setup_bot_commands', commands):
                with patch.object(
                    deals_scheduler,
                    'start_deals_scheduler',
                    scheduler,
                ):
                    with self.assertRaises(BrowserProcessIsolationError):
                        await deals_scheduler.bot_post_init(FakeApplication())

        commands.assert_not_awaited()
        scheduler.assert_not_awaited()

    def test_bot_entrypoint_wires_the_shutdown_callback(self) -> None:
        from bot import deals_scheduler, main

        self.assertIs(
            deals_scheduler.bot_post_shutdown,
            main.bot_post_shutdown,
        )


if __name__ == '__main__':
    unittest.main()
