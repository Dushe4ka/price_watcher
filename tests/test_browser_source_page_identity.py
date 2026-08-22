from __future__ import annotations

import unittest
from pathlib import Path

from src.captcha.models import ChallengeResolution
from src.marketplaces.contracts import (
    CategoryRequest,
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.sources.browser import (
    OzonBrowserSource,
    WildberriesBrowserSource,
    YandexMarketBrowserSource,
)
from tests.browser_source_fakes import (
    FakeCoordinator,
    FakeManager,
    FakePage,
    RedirectOnContentPage,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'marketplaces'
OZON_API_URL = 'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'


def _fixture(path: str) -> str:
    return (FIXTURES / path).read_text(encoding='utf-8')


class BrowserSourcePageIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_page_is_used_end_to_end_for_all_marketplaces(
        self,
    ) -> None:
        cases = (
            (
                OzonBrowserSource,
                '910001',
                FakePage(
                    html='<html>Ozon</html>',
                    evaluation={
                        'status': 200,
                        'url': OZON_API_URL,
                        'body': _fixture('ozon/success.json'),
                    },
                ),
            ),
            (
                WildberriesBrowserSource,
                '920001',
                FakePage(
                    html=_fixture('wildberries/success.html'),
                    evaluation={
                        'priceCurrent': '750',
                        'pageTitle': (
                            'Synthetic WB Item 920001 купить за 750 ₽'
                        ),
                    },
                ),
            ),
            (
                YandexMarketBrowserSource,
                '930001',
                FakePage(html=_fixture('yandex_market/success.html')),
            ),
        )
        for source_type, product_id, page in cases:
            with self.subTest(source=source_type.__name__):
                coordinator = FakeCoordinator()
                source = source_type(FakeManager(page), coordinator)

                result = await source.parse_product(ProductRequest(product_id))

                self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
                self.assertTrue(coordinator.pages)
                self.assertTrue(
                    all(item is page for item in coordinator.pages),
                )
                touched = page.content_pages + page.evaluation_pages
                self.assertTrue(touched)
                self.assertTrue(all(item is page for item in touched))
                self.assertEqual(1, len({id(item) for item in touched}))

    async def test_search_query_is_encoded_without_arbitrary_url_input(
        self,
    ) -> None:
        query = 'кот & чай'
        cases = (
            (
                OzonBrowserSource,
                FakePage(
                    html='<html>Ozon</html>',
                    evaluation={
                        'status': 200,
                        'url': OZON_API_URL,
                        'body': _fixture('ozon/success.json'),
                    },
                ),
            ),
            (
                WildberriesBrowserSource,
                FakePage(
                    html=_fixture('wildberries/success.html'),
                    evaluation=[{
                        'nmId': '920001',
                        'title': 'Synthetic WB Item',
                        'priceCurrent': '750',
                    }],
                ),
            ),
            (
                YandexMarketBrowserSource,
                FakePage(html=_fixture('yandex_market/success.html')),
            ),
        )
        for source_type, page in cases:
            with self.subTest(source=source_type.__name__):
                source = source_type(FakeManager(page), FakeCoordinator())

                result = await source.search_products(
                    SearchRequest(query=query, limit=1),
                )

                self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
                emitted = '\n'.join(page.goto_urls + page.expressions)
                self.assertNotIn(query, emitted)
                encoded_query = (
                    '%25D0%25BA%25D0%25BE%25D1%2582%2B%2526%2B'
                    '%25D1%2587%25D0%25B0%25D0%25B9'
                    if source_type is OzonBrowserSource
                    else '%D0%BA%D0%BE%D1%82+%26+%D1%87%D0%B0%D0%B9'
                )
                self.assertIn(encoded_query, emitted)

    async def test_unsafe_redirect_variants_are_rejected(self) -> None:
        redirects = (
            'https://www.ozon.ru.attacker.invalid/product/910001/',
            'https://user@www.ozon.ru/product/910001/',
            'https://127.0.0.1/product/910001/',
            'https://www.ozon.ru:444/product/910001/',
        )
        for redirect in redirects:
            with self.subTest(redirect=redirect):
                page = FakePage(
                    html='unused',
                    evaluation={
                        'status': 200,
                        'url': OZON_API_URL,
                        'body': _fixture('ozon/success.json'),
                    },
                    redirect_url=redirect,
                )
                source = OzonBrowserSource(
                    FakeManager(page),
                    FakeCoordinator(),
                )

                result = await source.parse_product(ProductRequest('910001'))

                self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)

    async def test_arbitrary_product_url_cannot_be_supplied_as_identifier(
        self,
    ) -> None:
        page = FakePage(html='unused')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(
            ProductRequest('https://attacker.invalid/930001'),
        )

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)
        self.assertEqual([], page.goto_urls)

    async def test_untrusted_category_slug_never_becomes_navigation_url(
        self,
    ) -> None:
        page = FakePage(html='unused')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            category_urls={
                'trusted': 'https://market.yandex.ru/catalog--x/1',
            },
        )

        result = await source.crawl_category(
            CategoryRequest(
                category_slug='https://attacker.invalid/',
                limit=1,
            ),
        )

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)
        self.assertEqual([], page.goto_urls)

    async def test_other_page_state_cannot_supply_ozon_capture(self) -> None:
        leased = FakePage(
            html='unused',
            evaluation={
                'status': 200,
                'url': OZON_API_URL,
                'body': _fixture('ozon/drift.json'),
            },
        )
        other = FakePage(
            html='unused',
            evaluation={
                'status': 200,
                'url': OZON_API_URL,
                'body': _fixture('ozon/success.json'),
            },
        )
        source = OzonBrowserSource(
            FakeManager(leased),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual([], other.evaluation_pages)

    async def test_late_redirect_after_solved_challenge_is_rejected(
        self,
    ) -> None:
        page = RedirectOnContentPage(
            html=_fixture('yandex_market/success.html'),
            redirect_after_call=2,
        )
        coordinator = FakeCoordinator(
            ChallengeResolution.NO_CHALLENGE,
            ChallengeResolution.SOLVED,
        )
        source = YandexMarketBrowserSource(FakeManager(page), coordinator)

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)


if __name__ == '__main__':
    unittest.main()
