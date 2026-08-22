import unittest
from urllib.parse import parse_qs, urlsplit

from src.browser.allowlist import (
    CategoryUrlResolutionRequired,
    UnsafeMarketplaceUrl,
    build_marketplace_url,
    validate_main_frame_url,
)
from src.marketplaces.contracts import (
    CategoryRequest,
    ProductRequest,
    SearchRequest,
)


class BrowserAllowlistTests(unittest.TestCase):
    def test_product_urls_are_built_from_typed_identifiers(self) -> None:
        cases = (
            (
                'ozon',
                'https://www.ozon.ru/product/item%2Fwith%20space/',
            ),
            (
                'wildberries',
                (
                    'https://www.wildberries.ru/catalog/'
                    'item%2Fwith%20space/detail.aspx'
                ),
            ),
            (
                'yandex_market',
                'https://market.yandex.ru/card/x/item%2Fwith%20space',
            ),
        )
        request = ProductRequest('item/with space')

        for marketplace, expected in cases:
            with self.subTest(marketplace=marketplace):
                self.assertEqual(
                    expected,
                    build_marketplace_url(marketplace, request),
                )

    def test_search_query_is_encoded_as_one_parameter(self) -> None:
        request = SearchRequest(query='phone&redirect=attacker', limit=2)

        for marketplace in ('ozon', 'wildberries', 'yandex_market'):
            with self.subTest(marketplace=marketplace):
                url = build_marketplace_url(marketplace, request)
                query = parse_qs(urlsplit(url).query)
                values = {
                    value
                    for parameter_values in query.values()
                    for value in parameter_values
                }

                self.assertIn('phone&redirect=attacker', values)
                validate_main_frame_url(marketplace, url)

    def test_category_request_requires_trusted_configuration_resolution(
        self,
    ) -> None:
        with self.assertRaises(CategoryUrlResolutionRequired):
            build_marketplace_url(
                'ozon',
                CategoryRequest(category_slug='electronics', limit=5),
            )

    def test_exact_allowlisted_https_hosts_are_accepted(self) -> None:
        cases = (
            ('ozon', 'https://www.ozon.ru/product/1'),
            ('ozon', 'https://www.ozon.ru:443/search/?text=phone'),
            ('wildberries', 'https://www.wildberries.ru/catalog/1'),
            ('yandex_market', 'https://market.yandex.ru/card/x/1'),
        )

        for marketplace, url in cases:
            with self.subTest(marketplace=marketplace, url=url):
                self.assertEqual(
                    url,
                    validate_main_frame_url(marketplace, url),
                )

    def test_suffix_trick_is_rejected(self) -> None:
        with self.assertRaises(UnsafeMarketplaceUrl):
            validate_main_frame_url(
                'ozon',
                'https://www.ozon.ru.attacker.invalid/product/1',
            )

    def test_unsafe_url_components_are_rejected(self) -> None:
        cases = (
            ('ozon', 'http://www.ozon.ru/product/1'),
            ('ozon', 'https://user@www.ozon.ru/product/1'),
            ('ozon', 'https://www.ozon.ru:444/product/1'),
            ('ozon', 'https://127.0.0.1/product/1'),
            ('ozon', 'https://[::1]/product/1'),
            ('ozon', 'https://ozon.ru/product/1'),
            ('wildberries', 'https://wildberries.ru/catalog/1'),
            (
                'yandex_market',
                'https://evilmarket.yandex.ru/card/x/1',
            ),
            ('yandex_market', 'https://market.yandex.ru./card/x/1'),
        )

        for marketplace, url in cases:
            with self.subTest(marketplace=marketplace, url=url):
                with self.assertRaises(UnsafeMarketplaceUrl):
                    validate_main_frame_url(marketplace, url)

    def test_malformed_port_is_suppressed_behind_safe_error(self) -> None:
        sentinel = 'secret-port-sentinel'

        with self.assertRaises(UnsafeMarketplaceUrl) as context:
            validate_main_frame_url(
                'ozon',
                f'https://www.ozon.ru:{sentinel}/product/1',
            )

        self.assertNotIn(sentinel, str(context.exception))
        self.assertIsNone(context.exception.__cause__)
        self.assertTrue(context.exception.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
