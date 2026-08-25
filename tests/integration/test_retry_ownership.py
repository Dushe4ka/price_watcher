"""``SourceRetryExecutor`` stays the only retry owner in the real stack.

The browser leg of these tests is a real Chromium navigating through the
production source, so the attempt counts are counted where they actually
happen: at the fixture server socket.
"""

from __future__ import annotations

import unittest

from src.marketplaces.contracts import SourceName, SourceOutcome
from tests.integration.fixture_server import fixture_server
from tests.integration.harness import (
    RealBrowserTestCase,
    run_controlled_retry_flow,
)


class RetryOwnershipTests(RealBrowserTestCase):
    async def test_retry_budget_is_not_multiplied_by_fallback(self) -> None:
        counters, _ = await run_controlled_retry_flow(
            max_attempts=2,
            profile_dir=self.profile_dir(),
        )

        self.assertEqual({'public': 2, 'browser': 2, 'apify': 1}, counters)

    async def test_single_attempt_policy_disables_every_retry(self) -> None:
        counters, _ = await run_controlled_retry_flow(
            max_attempts=1,
            profile_dir=self.profile_dir(),
        )

        self.assertEqual({'public': 1, 'browser': 1, 'apify': 1}, counters)

    async def test_attempt_counts_are_reported_per_source(self) -> None:
        _, result = await run_controlled_retry_flow(
            max_attempts=2,
            profile_dir=self.profile_dir(),
        )

        reported = {
            attempt.source: attempt.transport_attempts
            for attempt in result.attempts
        }
        self.assertEqual(
            {
                SourceName.PUBLIC: 2,
                SourceName.BROWSER: 2,
                SourceName.APIFY: 1,
            },
            reported,
        )
        self.assertIsNone(result.selected_source)
        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)

    async def test_a_recovering_endpoint_is_retried_exactly_once(self) -> None:
        async with fixture_server('flaky', failures=1) as server:
            counters, result = await run_controlled_retry_flow(
                max_attempts=2,
                profile_dir=self.profile_dir(),
                server=server,
            )

            self.assertEqual(2, server.count())

        self.assertEqual(2, counters['browser'])
        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertEqual(SourceName.BROWSER, result.selected_source)


if __name__ == '__main__':
    unittest.main()
