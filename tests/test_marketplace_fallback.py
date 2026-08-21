from __future__ import annotations

import unittest

from src.marketplaces.contracts import (
    MarketplaceOperation,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
    source_empty,
    source_failure,
    source_success,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.fallback import SourceCall, execute_fallback


class MarketplaceFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_challenge_continues_to_browser_success(self) -> None:
        calls: list[str] = []

        async def blocked() -> SourceResult[tuple[str, ...]]:
            calls.append('public')
            return source_failure(
                SourceName.PUBLIC,
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_DETECTED,
            )

        async def solved() -> SourceResult[tuple[str, ...]]:
            calls.append('browser')
            return source_success(SourceName.BROWSER, ('item',))

        result = await execute_fallback(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            (
                SourceCall(SourceName.PUBLIC, blocked),
                SourceCall(SourceName.BROWSER, solved),
            ),
        )

        self.assertEqual(['public', 'browser'], calls)
        self.assertEqual(SourceName.BROWSER, result.selected_source)
        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertEqual(('item',), result.value)

    async def test_validated_empty_stops_fallback_chain(self) -> None:
        calls: list[str] = []

        async def empty() -> SourceResult[None]:
            calls.append('public')
            return source_empty(SourceName.PUBLIC)

        async def unexpected_browser_call() -> SourceResult[tuple[str, ...]]:
            calls.append('browser')
            return source_success(SourceName.BROWSER, ('unexpected',))

        result = await execute_fallback(
            'wildberries',
            MarketplaceOperation.SEARCH_PRODUCTS,
            (
                SourceCall(SourceName.PUBLIC, empty),
                SourceCall(SourceName.BROWSER, unexpected_browser_call),
            ),
        )

        self.assertEqual(['public'], calls)
        self.assertEqual(SourceOutcome.EMPTY, result.outcome)
        self.assertEqual(SourceName.PUBLIC, result.selected_source)
        self.assertEqual(1, len(result.attempts))

    async def test_failure_chain_aggregates_every_attempt(self) -> None:
        async def challenge() -> SourceResult[None]:
            return source_failure(
                SourceName.PUBLIC,
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_DETECTED,
            )

        async def unavailable() -> SourceResult[None]:
            return source_failure(
                SourceName.BROWSER,
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

        result = await execute_fallback(
            'yandex_market',
            MarketplaceOperation.PARSE_PRODUCT,
            (
                SourceCall(SourceName.PUBLIC, challenge),
                SourceCall(SourceName.BROWSER, unavailable),
            ),
        )

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertIsNone(result.selected_source)
        self.assertEqual(
            (SourceName.PUBLIC, SourceName.BROWSER),
            tuple(attempt.source for attempt in result.attempts),
        )

    async def test_duplicate_sources_are_rejected_without_invocation(
        self,
    ) -> None:
        calls: list[str] = []

        async def public() -> SourceResult[None]:
            calls.append('public')
            return source_empty(SourceName.PUBLIC)

        with self.assertRaisesRegex(ValueError, 'duplicate source'):
            await execute_fallback(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                (
                    SourceCall(SourceName.PUBLIC, public),
                    SourceCall(SourceName.PUBLIC, public),
                ),
            )

        self.assertEqual([], calls)

    async def test_mismatched_attempt_source_is_rejected(self) -> None:
        async def invalid_result() -> SourceResult[None]:
            return SourceResult(
                source=SourceName.BROWSER,
                outcome=SourceOutcome.EMPTY,
                value=None,
                attempt=SourceAttempt(
                    source=SourceName.BROWSER,
                    outcome=SourceOutcome.EMPTY,
                    duration_ms=0,
                    item_count=0,
                ),
            )

        with self.assertRaisesRegex(ValueError, 'source does not match'):
            await execute_fallback(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                (SourceCall(SourceName.PUBLIC, invalid_result),),
            )


if __name__ == '__main__':
    unittest.main()
