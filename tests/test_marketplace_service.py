"""Marketplace service composition, fallback wiring and lifecycle."""

from __future__ import annotations

import unittest
from dataclasses import fields
from typing import Any
from unittest.mock import patch

from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceName,
    SourceOutcome,
)
from src.marketplaces import service as service_module
from src.marketplaces.service import (
    MarketplaceService,
    close_marketplace_services,
    configure_marketplace_registry,
    get_marketplace_service,
    refresh_marketplace_category_urls,
    start_marketplace_services,
)
from tests.marketplace_service_fakes import (
    FakeClock,
    RecordingSleep,
    StubRegistry,
    StubSource,
    challenge,
    crawl_result,
    empty,
    parsed_product,
    rate_limited,
    retry_settings,
    success,
    transport_error,
)


def make_service(
    *,
    chain: tuple[SourceName, ...],
    sources: dict[SourceName, StubSource],
    marketplace: str = 'ozon',
    settings: Any = None,
    sleep: Any = None,
    clock: Any = None,
) -> tuple[MarketplaceService, StubRegistry]:
    registry = StubRegistry(
        tuple((name, sources[name]) for name in chain),
    )
    extra: dict[str, Any] = {}
    if settings is not None:
        extra['settings'] = settings
    if sleep is not None:
        extra['sleep'] = sleep
    if clock is not None:
        extra['clock'] = clock
    return MarketplaceService(marketplace, registry, **extra), registry


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


class ServiceRetryWiringTests(unittest.IsolatedAsyncioTestCase):
    """The service composes ``SourceRetryExecutor`` around every source."""

    async def test_transport_error_is_retried_inside_one_source(self) -> None:
        sleep = RecordingSleep()
        public = StubSource(
            SourceName.PUBLIC,
            transport_error(SourceName.PUBLIC),
            transport_error(SourceName.PUBLIC),
        )
        service, _ = make_service(
            chain=(SourceName.PUBLIC,),
            sources={SourceName.PUBLIC: public},
            settings=retry_settings(),
            sleep=sleep,
            clock=FakeClock(),
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(2, len(public.requests))
        self.assertEqual(1, len(result.attempts))
        self.assertEqual(2, result.attempts[0].transport_attempts)
        self.assertEqual([0.25], sleep.delays)

    async def test_rate_limited_is_retried_as_a_transport_blip(self) -> None:
        browser = StubSource(
            SourceName.BROWSER,
            rate_limited(SourceName.BROWSER),
            rate_limited(SourceName.BROWSER),
        )
        service, _ = make_service(
            chain=(SourceName.BROWSER,),
            sources={SourceName.BROWSER: browser},
            settings=retry_settings(),
            sleep=RecordingSleep(),
            clock=FakeClock(),
        )

        result = await service.crawl_category(
            CategoryRequest(category_slug='beauty', limit=5),
        )

        self.assertEqual(2, len(browser.requests))
        self.assertEqual(2, result.attempts[0].transport_attempts)

    async def test_a_recovered_source_never_falls_back(self) -> None:
        product = parsed_product()
        apify = StubSource(
            SourceName.APIFY,
            success(SourceName.APIFY, product),
        )
        public = StubSource(
            SourceName.PUBLIC,
            transport_error(SourceName.PUBLIC),
            success(SourceName.PUBLIC, product),
        )
        service, _ = make_service(
            chain=(SourceName.PUBLIC, SourceName.APIFY),
            sources={SourceName.PUBLIC: public, SourceName.APIFY: apify},
            settings=retry_settings(),
            sleep=RecordingSleep(),
            clock=FakeClock(),
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(SourceName.PUBLIC, result.selected_source)
        self.assertEqual([], apify.requests)
        self.assertEqual(2, result.attempts[0].transport_attempts)

    async def test_apify_keeps_its_single_attempt_policy(self) -> None:
        sleep = RecordingSleep()
        apify = StubSource(
            SourceName.APIFY,
            transport_error(SourceName.APIFY),
        )
        service, _ = make_service(
            chain=(SourceName.APIFY,),
            sources={SourceName.APIFY: apify},
            settings=retry_settings(),
            sleep=sleep,
            clock=FakeClock(),
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(1, len(apify.requests))
        self.assertEqual(1, result.attempts[0].transport_attempts)
        self.assertEqual([], sleep.delays)

    async def test_retry_budget_comes_from_configuration(self) -> None:
        sleep = RecordingSleep()
        public = StubSource(
            SourceName.PUBLIC,
            transport_error(SourceName.PUBLIC),
        )
        service, _ = make_service(
            chain=(SourceName.PUBLIC,),
            sources={SourceName.PUBLIC: public},
            settings=retry_settings(max_attempts=1),
            sleep=sleep,
            clock=FakeClock(),
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(1, len(public.requests))
        self.assertEqual(1, result.attempts[0].transport_attempts)
        self.assertEqual([], sleep.delays)

    async def test_retry_delay_comes_from_configuration(self) -> None:
        sleep = RecordingSleep()
        public = StubSource(
            SourceName.PUBLIC,
            transport_error(SourceName.PUBLIC),
            transport_error(SourceName.PUBLIC),
        )
        service, _ = make_service(
            chain=(SourceName.PUBLIC,),
            sources={SourceName.PUBLIC: public},
            settings=retry_settings(base_delay_ms=10, max_delay_ms=20),
            sleep=sleep,
            clock=FakeClock(),
        )

        await service.parse_product(ProductRequest('9000001'))

        self.assertEqual([0.01], sleep.delays)

    async def test_one_deadline_is_shared_by_the_whole_chain(self) -> None:
        sleep = RecordingSleep()
        public = StubSource(
            SourceName.PUBLIC,
            transport_error(SourceName.PUBLIC),
        )
        apify = StubSource(
            SourceName.APIFY,
            success(SourceName.APIFY, parsed_product()),
        )
        service, _ = make_service(
            chain=(SourceName.PUBLIC, SourceName.APIFY),
            sources={SourceName.PUBLIC: public, SourceName.APIFY: apify},
            settings=retry_settings(total_timeout_sec=30),
            sleep=sleep,
            clock=FakeClock(step=20.0),
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(1, len(public.requests))
        self.assertEqual([], apify.requests)
        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(0, result.attempts[1].transport_attempts)
        self.assertEqual([], sleep.delays)

    async def test_challenge_is_never_retried(self) -> None:
        browser = StubSource(
            SourceName.BROWSER,
            challenge(SourceName.BROWSER),
        )
        service, _ = make_service(
            chain=(SourceName.BROWSER,),
            sources={SourceName.BROWSER: browser},
            settings=retry_settings(),
            sleep=RecordingSleep(),
            clock=FakeClock(),
        )

        result = await service.parse_product(ProductRequest('9000001'))

        self.assertEqual(1, len(browser.requests))
        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(1, result.attempts[0].transport_attempts)


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

    async def test_start_validates_the_configured_registry(self) -> None:
        registry = StubRegistry(
            ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
        )
        configure_marketplace_registry(registry)

        await start_marketplace_services()

        self.assertEqual(1, registry.start_calls)

    async def test_start_after_shutdown_is_refused(self) -> None:
        configure_marketplace_registry(
            StubRegistry(
                ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
            ),
        )

        await close_marketplace_services()

        with self.assertRaises(RuntimeError):
            await start_marketplace_services()

    def test_refresh_delegates_to_the_registry(self) -> None:
        registry = StubRegistry(
            ((SourceName.BROWSER, StubSource(SourceName.BROWSER)),),
        )
        configure_marketplace_registry(registry)

        refresh_marketplace_category_urls()

        self.assertEqual(1, registry.refresh_calls)

    def test_refresh_never_composes_a_registry_on_its_own(self) -> None:
        configure_marketplace_registry(None)

        with patch.object(
            service_module,
            'build_default_registry',
            side_effect=AssertionError('registry must not be composed here'),
        ):
            refresh_marketplace_category_urls()

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
