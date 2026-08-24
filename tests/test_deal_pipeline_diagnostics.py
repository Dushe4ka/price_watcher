"""Source-level diagnostics folded into existing pipeline counters."""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager, ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.marketplaces.contracts import (
    MarketplaceOperation,
    MarketplaceResult,
    SourceAttempt,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.diagnostics import (
    accumulate_marketplace_diagnostics,
    accumulate_source_attempts,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.service import configure_marketplace_registry
from src.schemas.deal import CategoriesConfig, DealRunStats
from src.services import deal_pipeline
from src.services.deal_pipeline import DealPipeline
from src.services.discount_evaluator import DealAction, DiscountDecision
from src.services.market_price_checker import (
    MarketCheckResult,
    MarketCheckStatus,
)
from tests.marketplace_service_fakes import (
    StubRegistry,
    StubSource,
    challenge,
    crawl_result,
    success,
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

    def test_loose_attempts_are_counted_without_errors(self) -> None:
        stats = DealRunStats()

        accumulate_source_attempts(
            stats,
            (
                _attempt(
                    SourceName.BROWSER,
                    SourceOutcome.CHALLENGE,
                    error_code=SafeErrorCode.CHALLENGE_DETECTED,
                ),
                _attempt(
                    SourceName.APIFY,
                    SourceOutcome.SUCCESS,
                    item_count=1,
                ),
            ),
        )

        self.assertEqual(1, stats.challenges)
        self.assertEqual(1, stats.source_outcomes['browser']['challenge'])
        self.assertEqual(1, stats.source_outcomes['apify']['success'])
        self.assertEqual(0, stats.errors)
        self.assertEqual(0, stats.fallback_activations)

    def test_single_attempt_is_not_a_fallback_activation(self) -> None:
        stats = DealRunStats()

        accumulate_marketplace_diagnostics(stats, valid_empty_result())

        self.assertEqual(0, stats.fallback_activations)


class StubMarketChecker:
    """Market checker stub returning one prepared result with attempts."""

    def __init__(self, result: MarketCheckResult) -> None:
        self.result = result
        self.calls = 0

    async def check(
        self,
        product: object,
        source_marketplace: str,
        category_slug: str,
    ) -> MarketCheckResult:
        del product, source_marketplace, category_slug
        self.calls += 1
        return self.result


class StubEvaluator:
    """Evaluator stub that always asks for a market comparison."""

    @staticmethod
    def calc_parser_discount(product: object) -> int | None:
        del product
        return 30

    async def evaluate(
        self,
        session: object,
        product: object,
        average_price: object,
    ) -> DiscountDecision:
        del session, product, average_price
        return DiscountDecision(
            action=DealAction.POST,
            reason='parser_discount',
            parser_discount=30,
            database_discount=None,
            average_price=None,
        )


class MarketCheckDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        configure_marketplace_registry(None)

    async def test_market_check_attempts_reach_the_run_stats(self) -> None:
        browser = StubSource(
            SourceName.BROWSER,
            crawl=(
                success(
                    SourceName.BROWSER,
                    crawl_result(product_ids=('9000001',)),
                ),
            ),
        )
        configure_marketplace_registry(
            StubRegistry(((SourceName.BROWSER, browser),)),
        )
        checker = StubMarketChecker(
            MarketCheckResult(
                required=True,
                status=MarketCheckStatus.FAILED,
                reason='not_cheaper_than_market',
                source_attempts=(
                    _attempt(
                        SourceName.BROWSER,
                        SourceOutcome.CHALLENGE,
                        error_code=SafeErrorCode.CHALLENGE_DETECTED,
                    ),
                    _attempt(
                        SourceName.APIFY,
                        SourceOutcome.SUCCESS,
                        item_count=2,
                    ),
                ),
            ),
        )
        pipeline = DealPipeline(bot=None)
        pipeline._market_checker = checker
        pipeline._evaluator = StubEvaluator()
        stats = DealRunStats()

        with _patched_persistence():
            await pipeline._process_marketplace_category(
                session=None,
                stats=stats,
                marketplace='ozon',
                category_slug='beauty',
                hashtag='beauty',
            )

        self.assertEqual(1, checker.calls)
        self.assertEqual(1, stats.challenges)
        self.assertEqual(1, stats.source_outcomes['browser']['challenge'])
        self.assertEqual(1, stats.source_outcomes['apify']['success'])
        self.assertEqual(0, stats.errors)


def _patched_persistence() -> AbstractContextManager[None]:
    """Replace every CRUD touchpoint of the deal pipeline with stubs."""
    tracked = SimpleNamespace(
        get_or_create=AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    history = SimpleNamespace(
        add_record=AsyncMock(),
        get_average_price=AsyncMock(return_value=None),
    )
    moderation = SimpleNamespace(create=AsyncMock())
    stack = ExitStack()
    stack.enter_context(
        patch.object(deal_pipeline, 'tracked_product_crud', tracked),
    )
    stack.enter_context(
        patch.object(deal_pipeline, 'product_price_history_crud', history),
    )
    stack.enter_context(
        patch.object(deal_pipeline, 'deal_moderation_crud', moderation),
    )
    return stack


class PipelineConfigRefreshTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        configure_marketplace_registry(None)

    async def test_run_refreshes_the_category_map_once(self) -> None:
        registry = StubRegistry(
            ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
        )
        configure_marketplace_registry(registry)
        config = CategoriesConfig.model_validate(
            {
                'categories': [
                    {
                        'slug': slug,
                        'hashtag': slug,
                        'name': slug,
                        'marketplaces': [
                            {
                                'marketplace': 'ozon',
                                'crawl_url': f'/category/{slug}-1/',
                            },
                        ],
                    }
                    for slug in ('beauty', 'gadgets')
                ],
            },
        )
        history = SimpleNamespace(delete_older_than=AsyncMock(return_value=0))
        processed = AsyncMock()

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    deal_pipeline,
                    'load_categories_config',
                    return_value=config,
                ),
            )
            stack.enter_context(
                patch.object(
                    deal_pipeline,
                    'product_price_history_crud',
                    history,
                ),
            )
            stack.enter_context(
                patch.object(deal_pipeline, '_CATEGORY_DELAY_SEC', 0),
            )
            stack.enter_context(
                patch.object(
                    DealPipeline,
                    '_process_marketplace_category',
                    processed,
                ),
            )
            await DealPipeline(bot=None).run(session=None)

        self.assertEqual(1, registry.refresh_calls)
        self.assertEqual(2, processed.await_count)


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
