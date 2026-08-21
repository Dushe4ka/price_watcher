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

    def test_ozon_canonical_summary_without_link_is_valid_item(self) -> None:
        payload = {
            'layout': [{'component': 'syntheticGrid'}],
            'widgetStates': {
                'synthetic-grid': json.dumps({
                    'items': [{
                        'skuId': '940001',
                        'title': 'Synthetic linkless item',
                        'webPrice': {'price': '1250'},
                    }],
                }),
            },
        }
        self.assertEqual(
            ValidationState.VALID_WITH_ITEMS,
            validate_ozon_payload(payload),
        )

    def test_ozon_empty_mixed_with_invalid_widget_is_drift(self) -> None:
        invalid_widgets = (
            '{not-json',
            json.dumps({'syntheticUnknownCollection': [{'value': 1}]}),
        )
        for invalid_widget in invalid_widgets:
            with self.subTest(invalid_widget=invalid_widget):
                payload = {
                    'layout': [{'component': 'syntheticEmpty'}],
                    'widgetStates': {
                        'synthetic-empty': json.dumps({'items': []}),
                        'synthetic-invalid': invalid_widget,
                    },
                }
                self.assertEqual(
                    ValidationState.DRIFT,
                    validate_ozon_payload(payload),
                )

    def test_ozon_empty_mixed_with_unknown_product_node_is_drift(self) -> None:
        payload = {
            'layout': [{'component': 'syntheticEmpty'}],
            'widgetStates': {
                'synthetic-empty': json.dumps({'items': []}),
                'synthetic-unknown': json.dumps({
                    'newGrid': {
                        'entry': {
                            'skuId': '950001',
                        },
                    },
                }),
            },
        }
        self.assertEqual(
            ValidationState.DRIFT,
            validate_ozon_payload(payload),
        )

    def test_ozon_empty_allows_harmless_metadata_dict(self) -> None:
        payload = {
            'layout': [{'component': 'syntheticEmpty'}],
            'widgetStates': {
                'synthetic-empty': json.dumps({'items': []}),
                'synthetic-metadata': json.dumps({
                    'metadata': {
                        'id': 42,
                        'revision': 2,
                        'label': 'synthetic',
                    },
                }),
            },
        }
        self.assertEqual(
            ValidationState.VALID_EMPTY,
            validate_ozon_payload(payload),
        )

    def test_ozon_numeric_id_with_product_evidence_is_drift(self) -> None:
        payload = {
            'layout': [{'component': 'syntheticEmpty'}],
            'widgetStates': {
                'synthetic-empty': json.dumps({'items': []}),
                'synthetic-unknown': json.dumps({
                    'newGrid': {
                        'entry': {
                            'id': 950002,
                            'price': '2100',
                            'title': 'Synthetic unknown product',
                            'link': '/synthetic-product',
                        },
                    },
                }),
            },
        }
        self.assertEqual(
            ValidationState.DRIFT,
            validate_ozon_payload(payload),
        )

    def test_ozon_mapper_contract_failure_is_drift(self) -> None:
        payload = {
            'layout': [{'component': 'syntheticGrid'}],
            'widgetStates': {
                'synthetic-malformed': json.dumps({
                    'items': [{'action': []}],
                }),
            },
        }
        self.assertEqual(
            ValidationState.DRIFT,
            validate_ozon_payload(payload),
        )

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

    def test_wildberries_bare_empty_phrase_is_drift(self) -> None:
        html = '<html><body><p>товары не найдены</p></body></html>'
        self.assertEqual(
            ValidationState.DRIFT,
            validate_wb_dom_snapshot(html),
        )

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

    def test_yandex_bare_empty_phrase_is_drift(self) -> None:
        html = '<html><body><p>products not found</p></body></html>'
        self.assertEqual(
            ValidationState.DRIFT,
            validate_yandex_html(html),
        )


if __name__ == '__main__':
    unittest.main()
