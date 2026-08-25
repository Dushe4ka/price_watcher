"""Allowlisted marketplace telemetry never renders sensitive source data."""

from __future__ import annotations

import logging
import unittest
from decimal import Decimal
from typing import Any

from src.marketplaces.contracts import (
    MarketplaceOperation,
    MarketplaceResult,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    source_failure,
)
from src.marketplaces.errors import (
    MarketplaceSourceError,
    SafeErrorCode,
    bounded_retry_after_ms,
)
from src.marketplaces.telemetry import (
    SAFE_ATTEMPT_FIELDS,
    format_safe_fields,
    safe_attempt_fields,
    safe_attempt_rows,
    safe_exception_label,
    silence_transport_request_logs,
)
from src.parsers.base import ParsedProduct
from src.services.deal_pipeline import DealPipeline


SECRET = 'sentinel-secret'
QUERY_SENTINEL = f'{SECRET}-query'
URL_SENTINEL = f'https://market.invalid/product/{SECRET}-id?text={SECRET}'
PROXY_SENTINEL = f'http://user:{SECRET}@proxy.invalid:8080'
COOKIE_SENTINEL = f'sessionid={SECRET}; path=/'
BEARER_SENTINEL = f'Bearer {SECRET}'
CAPTCHA_SENTINEL = f'{SECRET}-captcha-token'
BODY_SENTINEL = f'<html><body>{SECRET}</body></html>'
TITLE_SENTINEL = f'{SECRET}-product-title'
PRODUCT_ID_SENTINEL = f'{SECRET}-product-id'

ALLOWLIST = {
    'marketplace',
    'operation',
    'source',
    'outcome',
    'duration_ms',
    'item_count',
    'transport_attempts',
    'error_code',
    'retry_after_ms',
}


def sentinel_product() -> ParsedProduct:
    """Build a product whose every text field carries the sentinel."""
    return ParsedProduct(
        external_id=PRODUCT_ID_SENTINEL,
        title=TITLE_SENTINEL,
        price=Decimal('100'),
        image_url=URL_SENTINEL,
        product_url=URL_SENTINEL,
    )


def result_with_sentinels() -> MarketplaceResult[Any]:
    """Build a successful result whose payload is packed with sentinels."""
    return MarketplaceResult(
        marketplace='ozon',
        operation=MarketplaceOperation.SEARCH_PRODUCTS,
        outcome=SourceOutcome.SUCCESS,
        value=(sentinel_product(),),
        attempts=(
            SourceAttempt(
                source=SourceName.PUBLIC,
                outcome=SourceOutcome.RATE_LIMITED,
                duration_ms=12,
                item_count=0,
                error_code=SafeErrorCode.RATE_LIMITED,
                retry_after_ms=5_000,
            ),
            SourceAttempt(
                source=SourceName.BROWSER,
                outcome=SourceOutcome.SUCCESS,
                duration_ms=34,
                item_count=1,
                transport_attempts=2,
            ),
        ),
        selected_source=SourceName.BROWSER,
    )


class HostileAttempt:
    """A duck-typed attempt whose every field is a sensitive string."""

    def __init__(self) -> None:
        self.source = PROXY_SENTINEL
        self.outcome = COOKIE_SENTINEL
        self.duration_ms = BEARER_SENTINEL
        self.item_count = CAPTCHA_SENTINEL
        self.transport_attempts = BODY_SENTINEL
        self.error_code = QUERY_SENTINEL
        self.retry_after_ms = URL_SENTINEL


class HostileResult:
    """A duck-typed result that tries to smuggle sentinels into telemetry."""

    def __init__(self) -> None:
        self.marketplace = URL_SENTINEL
        self.operation = QUERY_SENTINEL
        self.outcome = BODY_SENTINEL
        self.value = BODY_SENTINEL
        self.attempts = (HostileAttempt(),)
        self.selected_source = PROXY_SENTINEL


class SafeAttemptFieldsTests(unittest.TestCase):
    def test_safe_attempt_fields_use_exact_allowlist(self) -> None:
        fields = safe_attempt_fields(result_with_sentinels())
        self.assertEqual(ALLOWLIST, set(fields))
        self.assertNotIn(SECRET, repr(fields))

    def test_allowlist_constant_matches_rendered_keys(self) -> None:
        self.assertEqual(ALLOWLIST, set(SAFE_ATTEMPT_FIELDS))

    def test_selected_attempt_is_reported_by_default(self) -> None:
        fields = safe_attempt_fields(result_with_sentinels())
        self.assertEqual('ozon', fields['marketplace'])
        self.assertEqual('search_products', fields['operation'])
        self.assertEqual('browser', fields['source'])
        self.assertEqual('success', fields['outcome'])
        self.assertEqual(34, fields['duration_ms'])
        self.assertEqual(1, fields['item_count'])
        self.assertEqual(2, fields['transport_attempts'])
        self.assertIsNone(fields['error_code'])
        self.assertIsNone(fields['retry_after_ms'])

    def test_rows_expose_every_attempt_with_retry_after(self) -> None:
        rows = safe_attempt_rows(result_with_sentinels())
        self.assertEqual(2, len(rows))
        self.assertEqual(ALLOWLIST, set(rows[0]))
        self.assertEqual('public', rows[0]['source'])
        self.assertEqual('rate_limited', rows[0]['outcome'])
        self.assertEqual('rate_limited', rows[0]['error_code'])
        self.assertEqual(5_000, rows[0]['retry_after_ms'])
        self.assertNotIn(SECRET, repr(rows))

    def test_hostile_result_values_are_redacted(self) -> None:
        fields = safe_attempt_fields(HostileResult())
        self.assertEqual(ALLOWLIST, set(fields))
        self.assertNotIn(SECRET, repr(fields))
        self.assertNotIn(SECRET, format_safe_fields(fields))
        self.assertEqual('unknown', fields['marketplace'])
        self.assertEqual('unknown', fields['operation'])
        self.assertEqual('unknown', fields['outcome'])
        self.assertIsNone(fields['source'])
        self.assertEqual(0, fields['duration_ms'])
        self.assertIsNone(fields['error_code'])
        self.assertIsNone(fields['retry_after_ms'])

    def test_format_safe_fields_renders_one_safe_line(self) -> None:
        line = format_safe_fields(safe_attempt_fields(result_with_sentinels()))
        self.assertNotIn(SECRET, line)
        self.assertNotIn('\n', line)
        self.assertIn('marketplace=ozon', line)
        self.assertIn('outcome=success', line)

    def test_result_without_attempts_still_renders_allowlist(self) -> None:
        result = MarketplaceResult(
            marketplace='wildberries',
            operation=MarketplaceOperation.CRAWL_CATEGORY,
            outcome=SourceOutcome.EMPTY,
            value=None,
            attempts=(),
            selected_source=None,
        )
        fields = safe_attempt_fields(result)
        self.assertEqual(ALLOWLIST, set(fields))
        self.assertIsNone(fields['source'])
        self.assertEqual('empty', fields['outcome'])


class SafeExceptionLabelTests(unittest.TestCase):
    def test_exception_label_hides_the_message(self) -> None:
        exc = RuntimeError(f'{BODY_SENTINEL} {COOKIE_SENTINEL}')
        label = safe_exception_label(exc)
        self.assertEqual('RuntimeError', label)
        self.assertNotIn(SECRET, label)

    def test_exception_label_accepts_non_exceptions(self) -> None:
        self.assertEqual('str', safe_exception_label(BEARER_SENTINEL))


class RetryAfterContractTests(unittest.TestCase):
    def test_retry_after_ms_defaults_to_none(self) -> None:
        attempt = SourceAttempt(
            source=SourceName.PUBLIC,
            outcome=SourceOutcome.SUCCESS,
            duration_ms=1,
            item_count=1,
        )
        self.assertIsNone(attempt.retry_after_ms)

    def test_retry_after_ms_must_not_be_negative(self) -> None:
        with self.assertRaises(ValueError):
            SourceAttempt(
                source=SourceName.PUBLIC,
                outcome=SourceOutcome.RATE_LIMITED,
                duration_ms=1,
                item_count=0,
                error_code=SafeErrorCode.RATE_LIMITED,
                retry_after_ms=-1,
            )

    def test_retry_after_ms_requires_a_rate_limited_outcome(self) -> None:
        with self.assertRaises(ValueError):
            SourceAttempt(
                source=SourceName.PUBLIC,
                outcome=SourceOutcome.TRANSPORT_ERROR,
                duration_ms=1,
                item_count=0,
                error_code=SafeErrorCode.TRANSPORT_FAILED,
                retry_after_ms=1_000,
            )

    def test_source_failure_carries_retry_after(self) -> None:
        result = source_failure(
            SourceName.APIFY,
            SourceOutcome.RATE_LIMITED,
            SafeErrorCode.RATE_LIMITED,
            retry_after_ms=2_500,
        )
        self.assertEqual(2_500, result.attempt.retry_after_ms)

    def test_source_error_rejects_retry_after_on_other_outcomes(self) -> None:
        with self.assertRaises(ValueError):
            MarketplaceSourceError(
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
                retry_after_ms=1_000,
            )

    def test_bounded_retry_after_ms_clamps_and_rejects_garbage(self) -> None:
        self.assertIsNone(bounded_retry_after_ms(None))
        self.assertIsNone(bounded_retry_after_ms(COOKIE_SENTINEL))
        self.assertEqual(0, bounded_retry_after_ms('0'))
        self.assertEqual(5_000, bounded_retry_after_ms('5'))
        self.assertEqual(300_000, bounded_retry_after_ms('999999999999'))


class TransportLogSilencingTests(unittest.TestCase):
    def test_request_line_loggers_are_raised_to_warning(self) -> None:
        noisy = logging.getLogger('httpx')
        original = noisy.level
        noisy.setLevel(logging.INFO)
        try:
            silence_transport_request_logs()
            self.assertFalse(noisy.isEnabledFor(logging.INFO))
            self.assertTrue(noisy.isEnabledFor(logging.WARNING))
        finally:
            noisy.setLevel(original)


class LegacyLogLeakTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_post_log_hides_the_product_title(self) -> None:
        pipeline = DealPipeline()
        from src.core.config import settings

        original = settings.deals_enabled
        settings.deals_enabled = False
        try:
            with self.assertLogs(
                'src.services.deal_pipeline',
                level=logging.INFO,
            ) as captured:
                posted = await pipeline._post_to_channel(
                    sentinel_product(),
                    'ozon',
                    '#beauty',
                )
        finally:
            settings.deals_enabled = original
        self.assertEqual(0, posted)
        self.assertNotIn(SECRET, '\n'.join(captured.output))

    async def test_channel_not_configured_log_hides_the_title(self) -> None:
        pipeline = DealPipeline()
        from src.core.config import settings

        original = settings.telegram_channel_id
        settings.telegram_channel_id = ''
        try:
            with self.assertLogs(
                'src.services.deal_pipeline',
                level=logging.WARNING,
            ) as captured:
                posted = await pipeline._post_to_channel(
                    sentinel_product(),
                    'ozon',
                    '#beauty',
                )
        finally:
            settings.telegram_channel_id = original
        self.assertIsNone(posted)
        self.assertNotIn(SECRET, '\n'.join(captured.output))


if __name__ == '__main__':
    unittest.main()
