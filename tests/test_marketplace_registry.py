"""Composition of trusted category URLs and configured source chains."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.marketplaces.contracts import (
    CategoryRequest,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.registry import (
    MarketplaceSourceRegistry,
    build_category_urls,
)
from src.marketplaces.sources.apify import ApifySource
from src.marketplaces.sources.browser import OzonBrowserSource
from src.marketplaces.sources.public import (
    OzonPublicSource,
    YandexPublicSource,
)
from src.schemas.deal import CategoriesConfig
from tests.browser_source_fakes import FakeCoordinator, FakeManager, FakePage
from tests.test_marketplace_settings import make_settings


FIXTURES = Path(__file__).parent / 'fixtures' / 'marketplaces'
OZON_API_URL = 'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'


def _fixture(path: str) -> str:
    return (FIXTURES / path).read_text(encoding='utf-8')


def _categories_config() -> CategoriesConfig:
    return CategoriesConfig.model_validate(
        {
            'categories': [
                {
                    'slug': 'beauty',
                    'hashtag': 'beauty',
                    'name': 'Beauty',
                    'marketplaces': [
                        {
                            'marketplace': 'ozon',
                            'crawl_url': '/category/beauty-1/',
                        },
                        {
                            'marketplace': 'yandex_market',
                            'crawl_url': (
                                'https://market.yandex.ru/category/beauty'
                            ),
                        },
                        {
                            'marketplace': 'wildberries',
                            'crawl_url': 'https://attacker.invalid/catalog',
                        },
                    ],
                },
            ],
        },
    )


class CountingManager(FakeManager):
    """Lease fake that records how often the manager was really closed."""

    def __init__(self, page: FakePage) -> None:
        super().__init__(page)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _ozon_page() -> FakePage:
    return FakePage(
        html='<html>Ozon</html>',
        evaluation={
            'kind': 'body',
            'status': 200,
            'url': OZON_API_URL,
            'body': _fixture('ozon/success.json'),
        },
    )


def _registry(
    manager: FakeManager,
    **overrides: object,
) -> MarketplaceSourceRegistry:
    return MarketplaceSourceRegistry(
        settings=make_settings(**overrides),
        manager=manager,
        coordinator=FakeCoordinator(),
        category_urls=build_category_urls(_categories_config()),
    )


class CategoryUrlTrustTests(unittest.TestCase):
    def test_relative_ozon_path_becomes_an_allowlisted_url(self) -> None:
        urls = build_category_urls(_categories_config())

        self.assertEqual(
            'https://www.ozon.ru/category/beauty-1/',
            urls['ozon']['beauty'],
        )

    def test_absolute_configured_url_is_preserved(self) -> None:
        urls = build_category_urls(_categories_config())

        self.assertEqual(
            'https://market.yandex.ru/category/beauty',
            urls['yandex_market']['beauty'],
        )

    def test_url_outside_the_allowlist_is_dropped(self) -> None:
        urls = build_category_urls(_categories_config())

        self.assertEqual({}, urls['wildberries'])


class SourceChainCompositionTests(unittest.TestCase):
    def test_chain_order_follows_configured_sources(self) -> None:
        registry = _registry(FakeManager(_ozon_page()))

        names = tuple(
            name for name, _ in registry.sources_for('yandex_market')
        )

        self.assertEqual(
            (SourceName.PUBLIC, SourceName.BROWSER, SourceName.APIFY),
            names,
        )

    def test_each_source_name_maps_to_its_adapter(self) -> None:
        registry = _registry(FakeManager(_ozon_page()))

        sources = dict(registry.sources_for('yandex_market'))

        self.assertIsInstance(sources[SourceName.PUBLIC], YandexPublicSource)
        self.assertIsInstance(sources[SourceName.APIFY], ApifySource)

    def test_disabled_public_adapter_is_used_for_ozon(self) -> None:
        registry = _registry(
            FakeManager(_ozon_page()),
            ozon_source_chain='public,browser',
        )

        sources = dict(registry.sources_for('ozon'))

        self.assertIsInstance(sources[SourceName.PUBLIC], OzonPublicSource)
        self.assertIsInstance(sources[SourceName.BROWSER], OzonBrowserSource)

    def test_adapters_are_built_once_per_marketplace(self) -> None:
        registry = _registry(FakeManager(_ozon_page()))

        first = dict(registry.sources_for('ozon'))
        second = dict(registry.sources_for('ozon'))

        self.assertIs(first[SourceName.BROWSER], second[SourceName.BROWSER])

    def test_unsupported_marketplace_is_rejected(self) -> None:
        registry = _registry(FakeManager(_ozon_page()))

        with self.assertRaises(ValueError):
            registry.sources_for('unknown_market')


class BrowserSourceInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_source_navigates_to_the_configured_url(
        self,
    ) -> None:
        page = _ozon_page()
        registry = _registry(FakeManager(page))
        source = dict(registry.sources_for('ozon'))[SourceName.BROWSER]

        result = await source.crawl_category(
            CategoryRequest(category_slug='beauty', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertIn(
            'url=/category/beauty-1/',
            ' '.join(page.expressions),
        )

    async def test_unknown_slug_fails_as_invalid_config(self) -> None:
        page = _ozon_page()
        registry = _registry(FakeManager(page))
        source = dict(registry.sources_for('ozon'))[SourceName.BROWSER]

        result = await source.crawl_category(
            CategoryRequest(category_slug='not-configured', limit=1),
        )

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)
        self.assertEqual(
            SafeErrorCode.INVALID_CONFIG,
            result.attempt.error_code,
        )
        self.assertEqual([], page.goto_urls)


class RegistryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_releases_the_manager_exactly_once(self) -> None:
        manager = CountingManager(_ozon_page())
        registry = _registry(manager)

        await registry.aclose()
        await registry.aclose()

        self.assertEqual(1, manager.close_calls)

    async def test_sources_are_refused_after_close(self) -> None:
        manager = CountingManager(_ozon_page())
        registry = _registry(manager)

        await registry.aclose()

        with self.assertRaises(RuntimeError):
            registry.sources_for('ozon')


if __name__ == '__main__':
    unittest.main()
