from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from src.parsers.ym_api import iter_ld_json_products


class ValidationState(StrEnum):
    VALID_WITH_ITEMS = 'valid_with_items'
    VALID_EMPTY = 'valid_empty'
    CHALLENGE = 'challenge'
    DRIFT = 'drift'


_CHALLENGE_MARKERS = (
    'access denied',
    'antibot',
    'captcha',
    'challenge',
    'confirm you are not a robot',
    'incidentid',
    'robot check',
    'проверка, что вы не робот',
    'подтвердите, что вы не робот',
)
_WB_ITEM_PATTERNS = (
    re.compile(r'data-nm-id=["\']\d+["\']', re.IGNORECASE),
    re.compile(r'/catalog/\d+/detail\.aspx', re.IGNORECASE),
    re.compile(r'class=["\'][^"\']*product-card', re.IGNORECASE),
)
_WB_EMPTY_MARKERS = (
    'catalog-empty',
    'search-empty',
    'ничего не найдено',
    'товары не найдены',
)
_YANDEX_EMPTY_MARKERS = (
    'searchempty',
    'search-empty',
    'ничего не нашли',
    'ничего не найдено',
    'products not found',
    'товары не найдены',
)


def validate_ozon_payload(payload: dict[str, Any]) -> ValidationState:
    """Classify a decoded Ozon widget payload without mapping products."""
    from src.ozon.parse_widgets import extract_product_ids

    serialized = json.dumps(payload, ensure_ascii=False, default=str).lower()
    if _contains_challenge(serialized):
        return ValidationState.CHALLENGE

    widget_states = payload.get('widgetStates')
    layout = payload.get('layout')
    if not isinstance(widget_states, dict) or not isinstance(
        layout,
        (dict, list),
    ):
        return ValidationState.DRIFT
    if extract_product_ids(payload, limit=1):
        return ValidationState.VALID_WITH_ITEMS

    structural_empty = not widget_states
    for raw_value in widget_states.values():
        value = _decode_json_value(raw_value)
        if value is None:
            continue
        if _has_nonempty_product_collection(value):
            return ValidationState.DRIFT
        if _has_empty_product_collection(value):
            structural_empty = True
    if structural_empty:
        return ValidationState.VALID_EMPTY
    return ValidationState.DRIFT


def validate_wb_dom_snapshot(html: str) -> ValidationState:
    """Classify a Wildberries HTML snapshot before DOM extraction."""
    normalized = html.lower()
    if _contains_challenge(normalized):
        return ValidationState.CHALLENGE
    if any(pattern.search(html) for pattern in _WB_ITEM_PATTERNS):
        return ValidationState.VALID_WITH_ITEMS
    if any(marker in normalized for marker in _WB_EMPTY_MARKERS):
        return ValidationState.VALID_EMPTY
    return ValidationState.DRIFT


def validate_yandex_html(html: str) -> ValidationState:
    """Classify Yandex Market HTML using its canonical JSON-LD walker."""
    normalized = html.lower()
    if _contains_challenge(normalized):
        return ValidationState.CHALLENGE
    if next(iter_ld_json_products(html), None) is not None:
        return ValidationState.VALID_WITH_ITEMS
    if any(marker in normalized for marker in _YANDEX_EMPTY_MARKERS):
        return ValidationState.VALID_EMPTY
    return ValidationState.DRIFT


validate_ozon_widget_payload = validate_ozon_payload
validate_yandex_market_html = validate_yandex_html


def _contains_challenge(normalized: str) -> bool:
    return any(marker in normalized for marker in _CHALLENGE_MARKERS)


def _decode_json_value(raw_value: Any) -> Any | None:
    if not isinstance(raw_value, str):
        return raw_value
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return None


def _has_empty_product_collection(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ('items', 'products', 'tiles', 'skuList', 'searchResults'):
        collection = value.get(key)
        if collection == [] or collection == {}:
            return True
    return False


def _has_nonempty_product_collection(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ('items', 'products', 'tiles', 'skuList', 'searchResults'):
        collection = value.get(key)
        if isinstance(collection, (dict, list)) and collection:
            return True
    return False
