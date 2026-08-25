"""Real-browser acceptance for the production browser fallback path.

Every test here launches a real Chromium through the production
``BrowserSessionManager`` → browser source → ``ChallengeCoordinator`` path.
Bytes come from the loopback fixture server; the production allowlist is
never modified and every request outside it is aborted by the router.
"""

from __future__ import annotations

import unittest

from src.marketplaces.contracts import SourceOutcome
from src.marketplaces.errors import SafeErrorCode
from tests.integration.fixture_server import fixture_server
from tests.integration.harness import (
    RealBrowserTestCase,
    run_controlled_browser_flow,
)


class BrowserFallbackFlowTests(RealBrowserTestCase):
    async def test_challenge_and_extraction_share_page_identity(self) -> None:
        async with fixture_server('challenge-then-result') as server:
            result, identities = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
            )

        self.assertEqual('success', result.outcome)
        self.assertEqual(1, len(set(identities)))
        self.assertGreaterEqual(len(identities), 3)
        self.assertEqual('1017', result.value.external_id)

    async def test_clean_page_parses_through_the_real_stack(self) -> None:
        async with fixture_server('clean') as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
            )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertEqual(1, result.attempt.item_count)
        self.assertIsNone(result.attempt.error_code)

    async def test_valid_empty_page_is_not_found_not_drift(self) -> None:
        async with fixture_server('valid-empty') as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
            )

        self.assertEqual(SourceOutcome.NOT_FOUND, result.outcome)
        self.assertIsNone(result.value)

    async def test_rate_limited_response_is_reported_as_rate_limited(
        self,
    ) -> None:
        async with fixture_server('rate-limit') as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
            )

        self.assertEqual(SourceOutcome.RATE_LIMITED, result.outcome)
        self.assertEqual(SafeErrorCode.RATE_LIMITED, result.attempt.error_code)

    async def test_server_error_is_reported_as_transport_error(self) -> None:
        async with fixture_server('transport-error') as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
            )

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(
            SafeErrorCode.TRANSPORT_FAILED,
            result.attempt.error_code,
        )

    async def test_slow_response_expires_the_operation_deadline(self) -> None:
        # The only wall-clock wait in the suite: the source deadline is set
        # to 1s and the fixture stalls for 5s, so the deadline is proven by
        # a real timeout rather than a patched clock.
        async with fixture_server('slow', delay_sec=5.0) as server:
            result, _ = await run_controlled_browser_flow(
                server,
                profile_dir=self.profile_dir(),
                total_timeout_sec=1.0,
            )

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)


if __name__ == '__main__':
    unittest.main()
