from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
    source_empty,
    source_failure,
    source_success,
)
from src.marketplaces.errors import SafeErrorCode


class RequestContractTests(unittest.TestCase):
    def test_requests_are_immutable_value_objects(self) -> None:
        request = SearchRequest(query='synthetic query', limit=3)

        self.assertEqual(1, request.page)
        with self.assertRaises(FrozenInstanceError):
            request.limit = 4

    def test_requests_keep_only_code_owned_navigation_inputs(self) -> None:
        self.assertEqual(
            CategoryRequest(
                category_slug='electronics', limit=2
            ).category_slug,
            'electronics',
        )
        self.assertEqual(
            ProductRequest(product_id='synthetic-1').product_id,
            'synthetic-1',
        )


class SourceResultTests(unittest.TestCase):
    def test_failure_cannot_carry_value(self) -> None:
        with self.assertRaises(ValueError):
            SourceResult(
                source=SourceName.BROWSER,
                outcome=SourceOutcome.CHALLENGE,
                value=('unexpected',),
                attempt=SourceAttempt(
                    source=SourceName.BROWSER,
                    outcome=SourceOutcome.CHALLENGE,
                    duration_ms=4,
                    item_count=0,
                ),
            )

    def test_success_requires_value(self) -> None:
        with self.assertRaises(ValueError):
            SourceResult(
                source=SourceName.PUBLIC,
                outcome=SourceOutcome.SUCCESS,
                value=None,
                attempt=SourceAttempt(
                    source=SourceName.PUBLIC,
                    outcome=SourceOutcome.SUCCESS,
                    duration_ms=4,
                    item_count=0,
                ),
            )

    def test_attempt_must_match_result_source_and_outcome(self) -> None:
        with self.assertRaisesRegex(ValueError, 'attempt source'):
            SourceResult(
                source=SourceName.BROWSER,
                outcome=SourceOutcome.CHALLENGE,
                value=None,
                attempt=SourceAttempt(
                    source=SourceName.APIFY,
                    outcome=SourceOutcome.CHALLENGE,
                    duration_ms=4,
                    item_count=0,
                ),
            )

    def test_factories_build_safe_results(self) -> None:
        success = source_success(SourceName.BROWSER, ('item',))
        empty = source_empty(SourceName.PUBLIC)
        failure = source_failure(
            SourceName.APIFY,
            SourceOutcome.TRANSPORT_ERROR,
            SafeErrorCode.TRANSPORT_FAILED,
        )

        self.assertEqual(SourceOutcome.SUCCESS, success.outcome)
        self.assertEqual(('item',), success.value)
        self.assertEqual(SourceOutcome.EMPTY, empty.outcome)
        self.assertIsNone(empty.value)
        self.assertEqual(SafeErrorCode.TRANSPORT_FAILED,
                         failure.attempt.error_code)


if __name__ == '__main__':
    unittest.main()
