"""Unit checks for WB DOM-card parsing (no network, no Playwright).

Run: python -m unittest tests.test_wb_crawler -v
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from src.wb.dom_extract import (
    card_to_parsed_product,
    detail_to_parsed_product,
    extract_title_from_page_title,
    parse_price,
    parse_rating,
    parse_review_count,
)


class WbCardParsingTests(unittest.TestCase):
    def test_card_with_discount(self) -> None:
        raw = {
            'nmId': '1317183427',
            'title': 'Платье вечернее ципао MORARU FAMILY',
            'brand': 'MORARU FAMILY',
            'imageUrl': (
                'https://basket-45.wbbasket.ru/vol13171/part1317183/'
                '1317183427/images/c516x688/1.webp'
            ),
            'priceCurrent': '3\xa0843\xa0₽',
            'priceOld': '12\xa0031\xa0₽',
            'ratingValue': '4,9',
            'reviewText': '360 оценок',
        }
        product = card_to_parsed_product(raw)
        assert product is not None
        self.assertEqual(product.external_id, '1317183427')
        self.assertEqual(product.price, Decimal('3843'))
        self.assertEqual(product.original_price, Decimal('12031'))
        self.assertEqual(product.discount_percent, 68)
        self.assertEqual(product.rating, 4.9)
        self.assertEqual(product.review_count, 360)
        self.assertEqual(
            product.product_url,
            'https://www.wildberries.ru/catalog/1317183427/detail.aspx',
        )

    def test_card_without_discount(self) -> None:
        raw = {
            'nmId': '999',
            'title': 'Товар без скидки',
            'brand': None,
            'imageUrl': None,
            'priceCurrent': '1\xa0323\xa0₽',
            'priceOld': None,
            'ratingValue': None,
            'reviewText': None,
        }
        product = card_to_parsed_product(raw)
        assert product is not None
        self.assertEqual(product.price, Decimal('1323'))
        self.assertIsNone(product.original_price)
        self.assertIsNone(product.discount_percent)
        self.assertIsNone(product.rating)
        self.assertIsNone(product.review_count)

    def test_card_without_price_is_skipped(self) -> None:
        raw = {'nmId': '1', 'title': 'No price', 'priceCurrent': None}
        self.assertIsNone(card_to_parsed_product(raw))

    def test_card_without_id_is_skipped(self) -> None:
        raw = {'nmId': None, 'title': 'x', 'priceCurrent': '100 ₽'}
        self.assertIsNone(card_to_parsed_product(raw))


class WbDetailParsingTests(unittest.TestCase):
    def test_extract_title_from_page_title(self) -> None:
        page_title = (
            'Платье вечернее ципао MORARU FAMILY 1317183427 купить за '
            '3 922 ₽ в интернет‑магазине Wildberries'
        )
        title = extract_title_from_page_title(page_title, '1317183427')
        self.assertEqual(title, 'Платье вечернее ципао MORARU FAMILY')

    def test_extract_title_fallback_without_marker(self) -> None:
        title = extract_title_from_page_title('Просто заголовок', '123')
        self.assertEqual(title, 'Просто заголовок')

    def test_detail_to_parsed_product(self) -> None:
        raw = {
            'priceCurrent': '3\xa0922\xa0₽',
            'priceOld': '12\xa0031\xa0₽',
            'ratingValue': '4,9',
            'reviewText': '360 оценок',
            'imageUrl': 'https://basket-45.wbbasket.ru/.../1.webp',
            'pageTitle': (
                'Платье вечернее ципао MORARU FAMILY 1317183427 купить за '
                '3 922 ₽ в интернет‑магазине Wildberries'
            ),
        }
        product = detail_to_parsed_product(raw, '1317183427')
        assert product is not None
        self.assertEqual(product.title, 'Платье вечернее ципао MORARU FAMILY')
        self.assertEqual(product.price, Decimal('3922'))
        self.assertEqual(product.original_price, Decimal('12031'))

    def test_detail_without_price_returns_none(self) -> None:
        raw = {'priceCurrent': None, 'pageTitle': 'x'}
        self.assertIsNone(detail_to_parsed_product(raw, '1'))


class WbFieldParsersTests(unittest.TestCase):
    def test_parse_price(self) -> None:
        self.assertEqual(parse_price('1\xa0599\xa0₽'), Decimal('1599'))
        self.assertIsNone(parse_price(None))
        self.assertIsNone(parse_price(''))

    def test_parse_rating(self) -> None:
        self.assertEqual(parse_rating('4,9'), 4.9)
        self.assertIsNone(parse_rating(None))

    def test_parse_review_count(self) -> None:
        self.assertEqual(parse_review_count('360 оценок'), 360)
        self.assertEqual(parse_review_count('1\xa0632 оценки'), 1632)
        self.assertIsNone(parse_review_count(None))


if __name__ == '__main__':
    unittest.main()
