"""API and bot shutdown release marketplace resources exactly once."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.marketplaces.contracts import SourceName
from src.marketplaces.service import (
    configure_marketplace_registry,
    configure_marketplace_runtime,
    marketplace_runtime_role,
)
from tests.marketplace_service_fakes import StubRegistry, StubSource


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


def _stub_registry() -> StubRegistry:
    registry = StubRegistry(
        ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
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
            with TestClient(main.app):
                pass

        self.assertEqual(1, registry.close_calls)

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

    def test_bot_entrypoint_wires_the_shutdown_callback(self) -> None:
        from bot import deals_scheduler, main

        self.assertIs(
            deals_scheduler.bot_post_shutdown,
            main.bot_post_shutdown,
        )


if __name__ == '__main__':
    unittest.main()
