"""Unit checks for WB search payload parsing (no network).

Run: python -m unittest tests.test_wb_crawler -v
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from src.parsers.wb_api import (
    extract_product_from_search,
    products_from_search_payload,
)


class WbPayloadParsingTests(unittest.TestCase):
    def test_nested_data_products_with_sale_price_u(self) -> None:
        payload = {
            'metadata': {'name': 'catalog'},
            'data': {
                'products': [
                    {
                        'id': 12345,
                        'name': 'Помада тестовая',
                        'salePriceU': 19900,
                        'priceU': 29900,
                        'totalQuantity': 12,
                        'reviewRating': 4.7,
                        'feedbacks': 10,
                        'sizes': [
                            {
                                'name': '',
                                'optionId': 1,
                            },
                        ],
                    },
                ],
            },
        }
        products = products_from_search_payload(payload)
        self.assertEqual(len(products), 1)
        parsed = extract_product_from_search(products[0])
        assert parsed is not None
        self.assertEqual(parsed.external_id, '12345')
        self.assertEqual(parsed.price, Decimal('199.00'))
        self.assertEqual(parsed.original_price, Decimal('299.00'))
        self.assertEqual(parsed.discount_percent, 33)
        self.assertTrue(parsed.in_stock)

    def test_classic_top_level_products_with_sizes_price(self) -> None:
        payload = {
            'products': [
                {
                    'id': 67890,
                    'name': 'Тушь классика',
                    'totalQuantity': 3,
                    'sizes': [
                        {
                            'price': {
                                'basic': 50000,
                                'product': 35000,
                            },
                        },
                    ],
                },
            ],
        }
        products = products_from_search_payload(payload)
        self.assertEqual(len(products), 1)
        parsed = extract_product_from_search(products[0])
        assert parsed is not None
        self.assertEqual(parsed.external_id, '67890')
        self.assertEqual(parsed.price, Decimal('350.00'))
        self.assertEqual(parsed.original_price, Decimal('500.00'))
        self.assertEqual(parsed.discount_percent, 30)

    def test_empty_payload(self) -> None:
        self.assertEqual(products_from_search_payload({}), [])
        self.assertEqual(
            products_from_search_payload({'data': {'products': []}}),
            [],
        )

    def test_skips_items_without_price(self) -> None:
        raw = {'id': 1, 'name': 'No price', 'sizes': [{'name': 'S'}]}
        self.assertIsNone(extract_product_from_search(raw))


if __name__ == '__main__':
    unittest.main()
