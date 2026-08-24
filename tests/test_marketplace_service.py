"""Marketplace service composition, fallback wiring and lifecycle."""

from __future__ import annotations

import unittest
from dataclasses import fields

from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.service import (
    MarketplaceService,
    close_marketplace_services,
    configure_marketplace_registry,
    get_marketplace_service,
)
from tests.marketplace_service_fakes import (
    StubRegistry,
    StubSource,
    challenge,
    crawl_result,
    empty,
    parsed_product,
    success,
)


def make_service(
    *,
    chain: tuple[SourceName, ...],
    sources: dict[SourceName, StubSource],
    marketplace: str = 'ozon',
) -> tuple[MarketplaceService, StubRegistry]:
    registry = StubRegistry(
        tuple((name, sources[name]) for name in chain),
    )
    return MarketplaceService(marketplace, registry), registry


class ServiceFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_falls_back_browser_to_apify(self) -> None:
        product = parsed_product()
        service, _ = make_service(
            chain=(SourceName.BROWSER, SourceName.APIFY),
            sources={
                SourceName.BROWSER: StubSource(
                    SourceName.BROWSER,
                    challenge(SourceName.BROWSER),
                ),
                SourceName.APIFY: StubSource(
                    SourceName.APIFY,
                    success(SourceName.APIFY, product),
                ),
            },
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(SourceName.APIFY, result.selected_source)
        self.assertEqual(2, len(result.attempts))
        self.assertEqual(product, result.value)
        self.assertEqual(
            MarketplaceOperation.PARSE_PRODUCT,
            result.operation,
        )

    async def test_first_terminal_source_stops_the_chain(self) -> None:
        apify = StubSource(
            SourceName.APIFY,
            success(SourceName.APIFY, parsed_product()),
        )
        service, _ = make_service(
            chain=(SourceName.BROWSER, SourceName.APIFY),
            sources={
                SourceName.BROWSER: StubSource(
                    SourceName.BROWSER,
                    empty(SourceName.BROWSER),
                ),
                SourceName.APIFY: apify,
            },
        )

        result = await service.search_products(
            SearchRequest(query='synthetic', limit=3),
        )

        self.assertEqual(SourceOutcome.EMPTY, result.outcome)
        self.assertEqual(SourceName.BROWSER, result.selected_source)
        self.assertEqual([], apify.requests)

    async def test_crawl_category_forwards_the_typed_request(self) -> None:
        browser = StubSource(
            SourceName.BROWSER,
            success(SourceName.BROWSER, crawl_result()),
        )
        service, _ = make_service(
            chain=(SourceName.BROWSER,),
            sources={SourceName.BROWSER: browser},
        )
        request = CategoryRequest(category_slug='beauty', limit=5)

        result = await service.crawl_category(request)

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertEqual([request], browser.requests)
        self.assertEqual(
            MarketplaceOperation.CRAWL_CATEGORY,
            result.operation,
        )

    async def test_exhausted_chain_reports_the_last_outcome(self) -> None:
        service, _ = make_service(
            chain=(SourceName.BROWSER, SourceName.APIFY),
            sources={
                SourceName.BROWSER: StubSource(
                    SourceName.BROWSER,
                    challenge(SourceName.BROWSER),
                ),
                SourceName.APIFY: StubSource(
                    SourceName.APIFY,
                    challenge(SourceName.APIFY),
                ),
            },
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertIsNone(result.selected_source)
        self.assertIsNone(result.value)
        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)


class CategoryTrustBoundaryTests(unittest.TestCase):
    def test_category_request_cannot_carry_a_url(self) -> None:
        names = tuple(item.name for item in fields(CategoryRequest))

        self.assertEqual(('category_slug', 'limit'), names)


class ServiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        configure_marketplace_registry(None)

    async def test_accessor_reuses_one_service_per_marketplace(self) -> None:
        registry = StubRegistry(
            ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
        )
        configure_marketplace_registry(registry)

        first = get_marketplace_service('ozon')
        second = get_marketplace_service('ozon')

        self.assertIs(first, second)
        self.assertIsNot(first, get_marketplace_service('wildberries'))

    async def test_close_releases_resources_exactly_once(self) -> None:
        registry = StubRegistry(
            ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
        )
        configure_marketplace_registry(registry)
        get_marketplace_service('ozon')

        await close_marketplace_services()
        await close_marketplace_services()

        self.assertEqual(1, registry.close_calls)

    async def test_service_close_is_idempotent(self) -> None:
        service, registry = make_service(
            chain=(SourceName.BROWSER,),
            sources={SourceName.BROWSER: StubSource(SourceName.BROWSER)},
        )

        await service.aclose()
        await service.aclose()

        self.assertEqual(1, registry.close_calls)

    async def test_services_are_refused_after_shutdown(self) -> None:
        configure_marketplace_registry(
            StubRegistry(
                ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
            ),
        )

        await close_marketplace_services()

        with self.assertRaises(RuntimeError):
            get_marketplace_service('ozon')


if __name__ == '__main__':
    unittest.main()
