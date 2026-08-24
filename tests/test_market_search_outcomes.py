"""Market comparison search over structured marketplace outcomes."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from src.marketplaces.contracts import (
    ProductRequest,
    SearchRequest,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.service import configure_marketplace_registry
from src.services.market_search import (
    collect_market_prices,
    fetch_market_prices,
    search_product_ids,
    search_products_result,
)
from tests.marketplace_service_fakes import (
    StubRegistry,
    StubSource,
    challenge,
    parsed_product,
    success,
)


def _install(source: StubSource) -> StubRegistry:
    registry = StubRegistry(((source.source, source),))
    configure_marketplace_registry(registry)
    return registry


class MarketSearchOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._delay = patch(
            'src.services.market_search._MARKETPLACE_DELAY_SEC',
            0,
        )
        self._delay.start()

    def tearDown(self) -> None:
        self._delay.stop()
        configure_marketplace_registry(None)

    async def test_search_returns_ids_on_success(self) -> None:
        products = (parsed_product('9000001'), parsed_product('9000002'))
        _install(
            StubSource(
                SourceName.BROWSER,
                search=(success(SourceName.BROWSER, products),),
            ),
        )

        product_ids = await search_product_ids('ozon', 'synthetic', limit=2)

        self.assertEqual(['9000001', '9000002'], product_ids)

    async def test_challenge_yields_no_ids_without_raising(self) -> None:
        _install(
            StubSource(
                SourceName.BROWSER,
                search=(challenge(SourceName.BROWSER),),
            ),
        )

        product_ids = await search_product_ids('ozon', 'synthetic', limit=2)

        self.assertEqual([], product_ids)

    async def test_structured_search_exposes_the_outcome(self) -> None:
        _install(
            StubSource(
                SourceName.BROWSER,
                search=(challenge(SourceName.BROWSER),),
            ),
        )

        result = await search_products_result('ozon', 'synthetic', limit=2)

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(1, len(result.attempts))


class MarketPriceCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._delay = patch(
            'src.services.market_search._MARKETPLACE_DELAY_SEC',
            0,
        )
        self._delay.start()

    def tearDown(self) -> None:
        self._delay.stop()
        configure_marketplace_registry(None)

    async def test_prices_are_collected_from_other_marketplaces(self) -> None:
        candidate = parsed_product('9000002', price='900')
        _install(
            StubSource(
                SourceName.BROWSER,
                search=(success(SourceName.BROWSER, (candidate,)),),
                product=(success(SourceName.BROWSER, candidate),),
            ),
        )
        product = parsed_product('9000001', price='1500')

        outcome = await collect_market_prices(
            product,
            'ozon',
            'Synthetic Item',
        )

        self.assertEqual(
            [Decimal('900'), Decimal('900')],
            list(outcome.prices),
        )
        self.assertEqual(
            ('wildberries', 'yandex_market'),
            outcome.marketplaces,
        )
        self.assertTrue(outcome.results)

    async def test_search_candidates_are_never_reparsed(self) -> None:
        candidates = (
            parsed_product('9000002', price='900'),
            parsed_product('9000003', price='950'),
        )
        source = StubSource(
            SourceName.BROWSER,
            search=(success(SourceName.BROWSER, candidates),),
        )
        _install(source)

        outcome = await collect_market_prices(
            parsed_product('9000001', price='1500'),
            'ozon',
            'Synthetic Item',
        )

        searches = [
            request
            for request in source.requests
            if isinstance(request, SearchRequest)
        ]
        products = [
            request
            for request in source.requests
            if isinstance(request, ProductRequest)
        ]
        self.assertEqual(2, len(searches))
        self.assertEqual([], products)
        self.assertEqual(2, len(outcome.results))
        self.assertEqual(4, len(outcome.prices))

    async def test_out_of_stock_candidates_are_ignored(self) -> None:
        candidate = parsed_product('9000002', price='900', in_stock=False)
        _install(
            StubSource(
                SourceName.BROWSER,
                search=(success(SourceName.BROWSER, (candidate,)),),
                product=(success(SourceName.BROWSER, candidate),),
            ),
        )

        prices, marketplaces = await fetch_market_prices(
            parsed_product('9000001', price='1500'),
            'ozon',
            'Synthetic Item',
        )

        self.assertEqual([], prices)
        self.assertEqual([], marketplaces)

    async def test_challenged_search_reports_its_outcome(self) -> None:
        _install(
            StubSource(
                SourceName.BROWSER,
                search=(challenge(SourceName.BROWSER),),
            ),
        )

        outcome = await collect_market_prices(
            parsed_product('9000001', price='1500'),
            'ozon',
            'Synthetic Item',
        )

        self.assertEqual((), outcome.prices)
        self.assertEqual(2, len(outcome.results))
        self.assertEqual(
            SourceOutcome.CHALLENGE,
            outcome.results[0].outcome,
        )


if __name__ == '__main__':
    unittest.main()
