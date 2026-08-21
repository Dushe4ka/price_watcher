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
_WB_EMPTY_PATTERNS = (
    re.compile(
        r'data-testid\s*=\s*["\'](?:catalog|search)-empty["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'class\s*=\s*["\'][^"\']*\b(?:catalog|search)-empty\b[^"\']*["\']',
        re.IGNORECASE,
    ),
)
_YANDEX_EMPTY_PATTERNS = (
    re.compile(
        r'data-zone-name\s*=\s*["\']searchempty["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'data-testid\s*=\s*["\']search-empty["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'class\s*=\s*["\'][^"\']*\bsearch-?empty\b[^"\']*["\']',
        re.IGNORECASE,
    ),
)
_OZON_PRODUCT_COLLECTION_KEYS = frozenset(
    ('items', 'products', 'tiles', 'skuList', 'searchResults')
)
_OZON_PRODUCT_IDENTITY_KEYS = frozenset(
    ('productId', 'sku', 'skuId')
)
_OZON_PRODUCT_COMMERCIAL_KEYS = frozenset(
    (
        'finalPrice',
        'originalPrice',
        'price',
        'priceWithoutDiscount',
        'salePrice',
        'webPrice',
    )
)
_OZON_PRODUCT_PRESENTATION_KEYS = frozenset(
    ('action', 'image', 'images', 'link', 'name', 'productTitle', 'title')
)
_INVALID_JSON = object()


def validate_ozon_payload(payload: dict[str, Any]) -> ValidationState:
    """Classify a decoded Ozon widget payload without mapping products."""
    from src.ozon.parse_widgets import extract_product_summary_map

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
    try:
        summaries = extract_product_summary_map(payload, limit=1)
    except Exception:
        return ValidationState.DRIFT
    if summaries:
        return ValidationState.VALID_WITH_ITEMS

    if not widget_states:
        return ValidationState.VALID_EMPTY

    structural_empty = False
    for raw_value in widget_states.values():
        value = _decode_json_value(raw_value)
        if value is _INVALID_JSON or not isinstance(value, dict):
            return ValidationState.DRIFT
        has_empty, has_invalid = _inspect_product_collections(value)
        if has_invalid:
            return ValidationState.DRIFT
        structural_empty = structural_empty or has_empty
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
    if any(pattern.search(html) for pattern in _WB_EMPTY_PATTERNS):
        return ValidationState.VALID_EMPTY
    return ValidationState.DRIFT


def validate_yandex_html(html: str) -> ValidationState:
    """Classify Yandex Market HTML using its canonical JSON-LD walker."""
    normalized = html.lower()
    if _contains_challenge(normalized):
        return ValidationState.CHALLENGE
    if next(iter_ld_json_products(html), None) is not None:
        return ValidationState.VALID_WITH_ITEMS
    if any(pattern.search(html) for pattern in _YANDEX_EMPTY_PATTERNS):
        return ValidationState.VALID_EMPTY
    return ValidationState.DRIFT


validate_ozon_widget_payload = validate_ozon_payload
validate_yandex_market_html = validate_yandex_html


def _contains_challenge(normalized: str) -> bool:
    return any(marker in normalized for marker in _CHALLENGE_MARKERS)


def _decode_json_value(raw_value: Any) -> Any:
    if not isinstance(raw_value, str):
        return raw_value
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return _INVALID_JSON


def _inspect_product_collections(value: dict[str, Any]) -> tuple[bool, bool]:
    if _is_product_like_mapping(value):
        return False, True
    has_empty = False
    for key, child in value.items():
        if key in _OZON_PRODUCT_COLLECTION_KEYS:
            if not isinstance(child, (dict, list)) or child:
                return has_empty, True
            has_empty = True
            continue
        if isinstance(child, list):
            if child:
                return has_empty, True
            continue
        if isinstance(child, dict):
            nested_empty, nested_invalid = _inspect_product_collections(child)
            if nested_invalid:
                return has_empty, True
            has_empty = has_empty or nested_empty
    return has_empty, False


def _is_product_like_mapping(value: dict[str, Any]) -> bool:
    keys = value.keys()
    if _OZON_PRODUCT_IDENTITY_KEYS.intersection(keys):
        return True
    raw_id = value.get('id')
    if (
        (
            isinstance(raw_id, int)
            and not isinstance(raw_id, bool)
        )
        or (
            isinstance(raw_id, str)
            and raw_id.isdigit()
        )
    ):
        return True
    return bool(
        _OZON_PRODUCT_COMMERCIAL_KEYS.intersection(keys)
        and _OZON_PRODUCT_PRESENTATION_KEYS.intersection(keys)
    )
