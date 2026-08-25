"""Real-browser proof for redirect rejection and content byte caps.

The unsafe redirect target is served with bait that would parse cleanly, so
following it would be visible as a success. The oversized scenario returns a
body far past the configured cap through a real response, and the same
fixture below the cap still parses — the cap is a boundary, not a wall.
"""

from __future__ import annotations

import unittest

from src.browser.allowlist import UnsafeMarketplaceUrl
from src.marketplaces.contracts import SourceOutcome
from src.marketplaces.errors import SafeErrorCode
from tests.integration.fixture_server import (
    UNSAFE_REDIRECT_HOST,
    UNSAFE_REDIRECT_URL,
    fixture_server,
)
from tests.integration.harness import (
    RealBrowserTestCase,
    run_controlled_browser_flow,
    run_lease_navigation,
)


class UnsafeRedirectTests(RealBrowserTestCase):
    async def test_lease_rejects_an_off_host_navigation(self) -> None:
        async with fixture_server('redirect-unsafe') as server:
            raised, hosts = await run_lease_navigation(
                server,
                profile_dir=self.profile_dir(),
                expected_url=UNSAFE_REDIRECT_URL,
            )

        self.assertIsInstance(raised, UnsafeMarketplaceUrl)
        # The off-host response really was reachable inside the controlled
        # topology, so the rejection is a decision and not a network fault.
        self.assertIn(UNSAFE_REDIRECT_HOST, hosts)

    async def test_lease_accepts_a_same_host_navigation(self) -> None:
        async with fixture_server('redirect-safe') as server:
            raised, hosts = await run_lease_navigation(
                server,
                profile_dir=self.profile_dir(),
                expected_url='https://market.yandex.ru/final',
            )

        self.assertIsNone(raised)
        self.assertEqual({'market.yandex.ru'}, set(hosts))

    async def test_source_never_returns_off_host_content(self) -> None:
        async with fixture_server('redirect-unsafe') as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
            )
            served = server.paths()

        self.assertNotEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertIsNone(result.value)
        self.assertIn(
            result.outcome,
            (SourceOutcome.INVALID_CONFIG, SourceOutcome.TRANSPORT_ERROR),
        )
        self.assertIn('/attacker', served)


class ContentLimitTests(RealBrowserTestCase):
    async def test_oversized_content_stops_at_the_byte_cap(self) -> None:
        async with fixture_server('oversized', filler=400_000) as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
                max_content_bytes=200_000,
            )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual(
            SafeErrorCode.CONTENT_TOO_LARGE,
            result.attempt.error_code,
        )
        self.assertIsNone(result.value)

    async def test_content_below_the_cap_is_still_parsed(self) -> None:
        async with fixture_server('oversized', filler=1_000) as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
                max_content_bytes=200_000,
            )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)


if __name__ == '__main__':
    unittest.main()
