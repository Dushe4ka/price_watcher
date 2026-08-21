from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.marketplaces.validation import (
    ValidationState,
    validate_ozon_payload,
    validate_wb_dom_snapshot,
    validate_yandex_html,
)


_FIXTURES = Path(__file__).parent / 'fixtures' / 'marketplaces'


def _load_text(path: str) -> str:
    return (_FIXTURES / path).read_text(encoding='utf-8')


class MarketplaceValidationTests(unittest.TestCase):
    def test_ozon_payload_states_are_distinct(self) -> None:
        cases = (
            ('success.json', ValidationState.VALID_WITH_ITEMS),
            ('empty.json', ValidationState.VALID_EMPTY),
            ('challenge.json', ValidationState.CHALLENGE),
            ('drift.json', ValidationState.DRIFT),
        )
        for fixture, expected in cases:
            with self.subTest(fixture=fixture):
                payload = json.loads(_load_text(f'ozon/{fixture}'))
                self.assertEqual(expected, validate_ozon_payload(payload))

    def test_wildberries_html_states_are_distinct(self) -> None:
        cases = (
            ('success.html', ValidationState.VALID_WITH_ITEMS),
            ('empty.html', ValidationState.VALID_EMPTY),
            ('challenge.html', ValidationState.CHALLENGE),
            ('drift.html', ValidationState.DRIFT),
        )
        for fixture, expected in cases:
            with self.subTest(fixture=fixture):
                html = _load_text(f'wildberries/{fixture}')
                self.assertEqual(expected, validate_wb_dom_snapshot(html))

    def test_yandex_html_states_are_distinct(self) -> None:
        cases = (
            ('success.html', ValidationState.VALID_WITH_ITEMS),
            ('empty.html', ValidationState.VALID_EMPTY),
            ('challenge.html', ValidationState.CHALLENGE),
            ('drift.html', ValidationState.DRIFT),
        )
        for fixture, expected in cases:
            with self.subTest(fixture=fixture):
                html = _load_text(f'yandex_market/{fixture}')
                self.assertEqual(expected, validate_yandex_html(html))


if __name__ == '__main__':
    unittest.main()
