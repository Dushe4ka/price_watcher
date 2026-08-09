"""Unit checks for Yandex Market HTML parsing (no network).

Run: python -m unittest tests.test_yandex_market_parser -v
"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from src.parsers.ym_api import (
    build_product_url,
    extract_offer_prices,
    iter_ld_json_products,
    product_id_from_ld_json,
)
from src.parsers.yandex_market import YandexMarketParser

_PRODUCT_LD_JSON = {
    '@type': 'Product',
    '@context': 'https://schema.org',
    'name': 'Тестовый товар',
    'sku': '101841125768',
    'image': 'https://avatars.mds.yandex.net/get-mpic/test/orig',
    'offers': {
        '@type': 'Offer',
        'availability': 'https://schema.org/InStock',
        'price': 123,
        'priceCurrency': 'RUB',
    },
    'aggregateRating': {
        '@type': 'AggregateRating',
        'ratingValue': 4.8,
        'ratingCount': 3300,
    },
}

_ITEM_LIST_LD_JSON = {
    '@type': 'ItemList',
    '@context': 'https://schema.org',
    'itemListElement': [
        {
            '@type': 'ListItem',
            'position': 1,
            'item': {
                '@type': 'Product',
                'name': 'Товар 1',
                'sku': '111',
                'url': 'https://market.yandex.ru/card/tovar-1/111',
                'offers': {'@type': 'Offer', 'price': 999},
            },
        },
        {
            '@type': 'ListItem',
            'position': 2,
            'item': {
                '@type': 'Product',
                'name': 'Товар 2',
                'sku': '222',
                'url': 'https://market.yandex.ru/card/tovar-2/222',
                'offers': {'@type': 'Offer', 'price': 555},
            },
        },
    ],
}


def _patch_block(market_sku: str, price: int, old_price: int | None) -> str:
    entry: dict = {'marketSku': market_sku, 'skuId': market_sku, 'price': price}
    if old_price is not None:
        entry['oldPrice'] = old_price
    payload = {'collections': {'offerAnalytics': {'anyKey': entry}}}
    return f'<noframes data-apiary="patch">{json.dumps(payload)}</noframes>'


def _html(*, ld_json: dict, patch: str = '') -> str:
    ld_block = (
        f'<script type="application/ld+json">{json.dumps(ld_json)}</script>'
    )
    return f'<html><body>{ld_block}{patch}</body></html>'


class YmApiTests(unittest.TestCase):
    def test_iter_ld_json_products_top_level(self) -> None:
        html = _html(ld_json=_PRODUCT_LD_JSON)
        products = list(iter_ld_json_products(html))
        self.assertEqual(len(products), 1)
        self.assertEqual(product_id_from_ld_json(products[0]), '101841125768')

    def test_iter_ld_json_products_nested_item_list(self) -> None:
        html = _html(ld_json=_ITEM_LIST_LD_JSON)
        products = list(iter_ld_json_products(html))
        ids = [product_id_from_ld_json(p) for p in products]
        self.assertEqual(ids, ['111', '222'])

    def test_extract_offer_prices_matches_by_sku(self) -> None:
        html = _html(
            ld_json=_PRODUCT_LD_JSON,
            patch=_patch_block('101841125768', 123, 499),
        )
        price, old_price = extract_offer_prices(html, '101841125768')
        self.assertEqual(price, Decimal('123'))
        self.assertEqual(old_price, Decimal('499'))

    def test_extract_offer_prices_no_patch_block(self) -> None:
        html = _html(ld_json=_PRODUCT_LD_JSON)
        price, old_price = extract_offer_prices(html, '101841125768')
        self.assertIsNone(price)
        self.assertIsNone(old_price)

    def test_build_product_url(self) -> None:
        self.assertEqual(
            build_product_url('123'),
            'https://market.yandex.ru/card/x/123',
        )


class YandexMarketParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = YandexMarketParser()

    def test_extract_product_id_from_card_url(self) -> None:
        url = 'https://market.yandex.ru/card/naushniki/101841125768'
        self.assertEqual(self.parser.extract_product_id(url), '101841125768')

    def test_extract_product_id_from_legacy_url(self) -> None:
        url = 'https://market.yandex.ru/product--naushniki/5705927210'
        self.assertEqual(self.parser.extract_product_id(url), '5705927210')

    def test_extract_product_id_invalid(self) -> None:
        with self.assertRaises(ValueError):
            self.parser.extract_product_id('https://example.com/foo')

    def test_extract_from_json_ld_uses_patch_discount(self) -> None:
        html = _html(
            ld_json=_PRODUCT_LD_JSON,
            patch=_patch_block('101841125768', 123, 499),
        )
        product = self.parser._extract_from_json_ld(
            _PRODUCT_LD_JSON, '101841125768', html,
        )
        self.assertEqual(product.price, Decimal('123'))
        self.assertEqual(product.original_price, Decimal('499'))
        self.assertEqual(product.discount_percent, 75)
        self.assertEqual(product.rating, 4.8)
        self.assertEqual(product.review_count, 3300)
        self.assertEqual(
            product.product_url,
            'https://market.yandex.ru/card/x/101841125768',
        )

    def test_extract_from_json_ld_without_discount(self) -> None:
        html = _html(ld_json=_PRODUCT_LD_JSON)
        product = self.parser._extract_from_json_ld(
            _PRODUCT_LD_JSON, '101841125768', html,
        )
        self.assertEqual(product.price, Decimal('123'))
        self.assertIsNone(product.original_price)
        self.assertIsNone(product.discount_percent)


if __name__ == '__main__':
    unittest.main()
