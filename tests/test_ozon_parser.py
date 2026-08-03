"""Unit checks for Ozon widget payload parsing (no network).

Run: python -m unittest tests.test_ozon_parser -v
"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from src.ozon.parse_widgets import (
    extract_product_ids,
    extract_product_summary_map,
)


def _payload_with_tiles() -> dict:
    tile_grid = {
        'items': [
            {
                'skuId': '111',
                'title': 'Крем тестовый',
                'link': '/product/krem-testovyi-111/',
                'webPrice': {
                    'price': '999 ₽',
                    'originalPrice': '1 499 ₽',
                },
                'coverImage': 'https://cdn.example/1.jpg',
                'rating': 4.8,
                'reviewsCount': 42,
            },
            {
                'id': 222,
                'productTitle': 'Тушь',
                'action': {'link': '/product/tush-222/'},
                'price': {'price': '350', 'originalPrice': '500'},
            },
        ],
    }
    return {
        'layout': [{'component': 'tileGrid2'}],
        'widgetStates': {
            'tileGrid2-123': json.dumps(tile_grid, ensure_ascii=False),
            'other-1': json.dumps({'hello': 'world'}),
        },
    }


class OzonWidgetParsingTests(unittest.TestCase):
    def test_extract_ids_from_nested_tiles(self) -> None:
        ids = extract_product_ids(_payload_with_tiles(), limit=5)
        self.assertEqual(ids, ['111', '222'])

    def test_extract_priced_summaries(self) -> None:
        summaries = extract_product_summary_map(_payload_with_tiles(), limit=5)
        self.assertEqual(set(summaries), {'111', '222'})
        first = summaries['111']
        self.assertEqual(first.price, Decimal('999'))
        self.assertEqual(first.original_price, Decimal('1499'))
        self.assertEqual(first.discount_percent, 33)
        self.assertEqual(first.title, 'Крем тестовый')
        self.assertEqual(first.rating, 4.8)
        self.assertEqual(first.review_count, 42)

        second = summaries['222']
        self.assertEqual(second.price, Decimal('350'))
        self.assertEqual(second.original_price, Decimal('500'))

    def test_empty_payload(self) -> None:
        self.assertEqual(extract_product_ids({}, 5), [])
        self.assertEqual(extract_product_summary_map({}, limit=5), {})

    def test_skips_tiles_without_price(self) -> None:
        payload = {
            'widgetStates': {
                'x': json.dumps({
                    'items': [
                        {
                            'skuId': '333',
                            'title': 'No price',
                            'link': '/product/no-price-333/',
                        },
                    ],
                }),
            },
        }
        self.assertEqual(extract_product_summary_map(payload, limit=5), {})
        self.assertEqual(extract_product_ids(payload, 5), ['333'])


if __name__ == '__main__':
    unittest.main()
