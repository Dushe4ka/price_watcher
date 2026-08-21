from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceResult,
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

    def test_product_request_repr_redacts_product_id(self) -> None:
        request = ProductRequest(product_id='product-id-sentinel')

        self.assertNotIn('product-id-sentinel', repr(request))

    def test_search_request_repr_redacts_query(self) -> None:
        request = SearchRequest(query='search-query-sentinel', limit=3)

        self.assertNotIn('search-query-sentinel', repr(request))


class SourceResultTests(unittest.TestCase):
    def test_zero_transport_attempts_accepts_only_timeout(
        self,
    ) -> None:
        timeout_attempt = SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=SourceOutcome.TRANSPORT_ERROR,
            duration_ms=0,
            item_count=0,
            error_code=SafeErrorCode.TIMEOUT,
            transport_attempts=0,
        )

        self.assertEqual(0, timeout_attempt.transport_attempts)

    def test_zero_transport_attempts_rejects_non_timeout_outcomes(
        self,
    ) -> None:
        invalid_attempts = (
            (SourceOutcome.SUCCESS, None),
            (SourceOutcome.EMPTY, None),
            (SourceOutcome.TRANSPORT_ERROR, SafeErrorCode.TRANSPORT_FAILED),
        )

        for outcome, error_code in invalid_attempts:
            with self.subTest(outcome=outcome, error_code=error_code):
                with self.assertRaisesRegex(ValueError, 'zero'):
                    SourceAttempt(
                        source=SourceName.PUBLIC,
                        outcome=outcome,
                        duration_ms=0,
                        item_count=0,
                        error_code=error_code,
                        transport_attempts=0,
                    )

    def test_transport_attempts_rejects_negative(self) -> None:
        with self.assertRaisesRegex(ValueError, 'not be negative'):
            SourceAttempt(
                source=SourceName.PUBLIC,
                outcome=SourceOutcome.TRANSPORT_ERROR,
                duration_ms=0,
                item_count=0,
                transport_attempts=-1,
            )

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

    def test_source_result_repr_redacts_value(self) -> None:
        result = SourceResult(
            source=SourceName.BROWSER,
            outcome=SourceOutcome.SUCCESS,
            value=('https://product-url-sentinel', 'product-title-sentinel'),
            attempt=SourceAttempt(
                source=SourceName.BROWSER,
                outcome=SourceOutcome.SUCCESS,
                duration_ms=4,
                item_count=1,
            ),
        )

        self.assertNotIn('https://product-url-sentinel', repr(result))
        self.assertNotIn('product-title-sentinel', repr(result))

    def test_marketplace_result_repr_redacts_value(self) -> None:
        result = MarketplaceResult(
            marketplace='ozon',
            operation=MarketplaceOperation.PARSE_PRODUCT,
            outcome=SourceOutcome.SUCCESS,
            value=('product-id-sentinel', 'image-url-sentinel'),
            attempts=(
                SourceAttempt(
                    source=SourceName.BROWSER,
                    outcome=SourceOutcome.SUCCESS,
                    duration_ms=4,
                    item_count=1,
                ),
            ),
            selected_source=SourceName.BROWSER,
        )

        self.assertNotIn('product-id-sentinel', repr(result))
        self.assertNotIn('image-url-sentinel', repr(result))


if __name__ == '__main__':
    unittest.main()
