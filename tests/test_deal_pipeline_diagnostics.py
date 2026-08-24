"""Source-level diagnostics folded into existing pipeline counters."""

from __future__ import annotations

import unittest

from src.marketplaces.contracts import (
    MarketplaceOperation,
    MarketplaceResult,
    SourceAttempt,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.diagnostics import accumulate_marketplace_diagnostics
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.service import configure_marketplace_registry
from src.schemas.deal import DealRunStats
from src.services.deal_pipeline import DealPipeline
from tests.marketplace_service_fakes import (
    StubRegistry,
    StubSource,
    challenge,
    crawl_result,
)


def _attempt(
    source: SourceName,
    outcome: SourceOutcome,
    *,
    item_count: int = 0,
    error_code: SafeErrorCode | None = None,
) -> SourceAttempt:
    return SourceAttempt(
        source=source,
        outcome=outcome,
        duration_ms=1,
        item_count=item_count,
        error_code=error_code,
    )


def _result(
    outcome: SourceOutcome,
    attempts: tuple[SourceAttempt, ...],
    *,
    selected_source: SourceName | None,
    marketplace: str = 'ozon',
    value: object = None,
) -> MarketplaceResult[object]:
    return MarketplaceResult(
        marketplace=marketplace,
        operation=MarketplaceOperation.CRAWL_CATEGORY,
        outcome=outcome,
        value=value,
        attempts=attempts,
        selected_source=selected_source,
    )


def valid_empty_result() -> MarketplaceResult[object]:
    """A structurally valid empty page, which is not a failure."""
    return _result(
        SourceOutcome.EMPTY,
        (_attempt(SourceName.BROWSER, SourceOutcome.EMPTY),),
        selected_source=SourceName.BROWSER,
    )


class DiagnosticsAccumulationTests(unittest.TestCase):
    def test_valid_empty_does_not_increment_pipeline_errors(self) -> None:
        stats = DealRunStats()

        accumulate_marketplace_diagnostics(stats, valid_empty_result())

        self.assertEqual(0, stats.errors)
        self.assertEqual(1, stats.source_outcomes['browser']['empty'])

    def test_terminal_failure_increments_error_counters(self) -> None:
        stats = DealRunStats()

        accumulate_marketplace_diagnostics(
            stats,
            _result(
                SourceOutcome.CHALLENGE,
                (
                    _attempt(
                        SourceName.BROWSER,
                        SourceOutcome.CHALLENGE,
                        error_code=SafeErrorCode.CHALLENGE_DETECTED,
                    ),
                ),
                selected_source=None,
            ),
        )

        self.assertEqual(1, stats.errors)
        self.assertEqual(1, stats.mp('ozon').errors)
        self.assertEqual(1, stats.challenges)

    def test_recovered_fallback_is_counted_without_errors(self) -> None:
        stats = DealRunStats()

        accumulate_marketplace_diagnostics(
            stats,
            _result(
                SourceOutcome.SUCCESS,
                (
                    _attempt(
                        SourceName.BROWSER,
                        SourceOutcome.CHALLENGE,
                        error_code=SafeErrorCode.CHALLENGE_DETECTED,
                    ),
                    _attempt(
                        SourceName.APIFY,
                        SourceOutcome.SUCCESS,
                        item_count=3,
                    ),
                ),
                selected_source=SourceName.APIFY,
                value=crawl_result(),
            ),
        )

        self.assertEqual(0, stats.errors)
        self.assertEqual(1, stats.fallback_activations)
        self.assertEqual(1, stats.challenges)
        self.assertEqual(1, stats.source_outcomes['apify']['success'])
        self.assertEqual(1, stats.source_outcomes['browser']['challenge'])

    def test_not_found_stays_an_error_for_the_pipeline(self) -> None:
        stats = DealRunStats()

        accumulate_marketplace_diagnostics(
            stats,
            _result(
                SourceOutcome.NOT_FOUND,
                (_attempt(SourceName.PUBLIC, SourceOutcome.NOT_FOUND),),
                selected_source=SourceName.PUBLIC,
            ),
        )

        self.assertEqual(1, stats.errors)
        self.assertEqual(1, stats.source_outcomes['public']['not_found'])

    def test_single_attempt_is_not_a_fallback_activation(self) -> None:
        stats = DealRunStats()

        accumulate_marketplace_diagnostics(stats, valid_empty_result())

        self.assertEqual(0, stats.fallback_activations)


class PipelineCrawlDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        configure_marketplace_registry(None)

    async def test_failed_crawl_records_diagnostics_and_stops(self) -> None:
        browser = StubSource(
            SourceName.BROWSER,
            challenge(SourceName.BROWSER),
        )
        configure_marketplace_registry(
            StubRegistry(((SourceName.BROWSER, browser),)),
        )
        stats = DealRunStats()
        pipeline = DealPipeline(bot=None)

        await pipeline._process_marketplace_category(
            session=None,
            stats=stats,
            marketplace='ozon',
            category_slug='beauty',
            hashtag='beauty',
        )

        self.assertEqual(0, stats.crawled)
        self.assertEqual(1, stats.errors)
        self.assertEqual(1, stats.source_outcomes['browser']['challenge'])

    async def test_crawl_request_carries_only_the_category_slug(self) -> None:
        browser = StubSource(
            SourceName.BROWSER,
            challenge(SourceName.BROWSER),
        )
        configure_marketplace_registry(
            StubRegistry(((SourceName.BROWSER, browser),)),
        )
        pipeline = DealPipeline(bot=None)

        await pipeline._process_marketplace_category(
            session=None,
            stats=DealRunStats(),
            marketplace='ozon',
            category_slug='beauty',
            hashtag='beauty',
        )

        self.assertEqual('beauty', browser.requests[0].category_slug)
        self.assertFalse(hasattr(browser.requests[0], 'crawl_url'))


if __name__ == '__main__':
    unittest.main()
