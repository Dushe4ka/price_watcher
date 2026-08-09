"""Shared Yandex Market page-parsing helpers (crawler + parser).

Product pages are still server-rendered — no headless browser needed.
Two independent data sources are embedded in the HTML:

- ``<script type="application/ld+json">`` — schema.org ``Product`` /
  ``ItemList`` nodes. Reliable for id/title/image/rating, but the current
  offer only exposes the live ``price`` (no pre-discount price).
- ``<noframes data-apiary="patch">`` blocks — plain JSON (not HTML-escaped)
  used by the SPA for hydration. Under ``collections.offerAnalytics`` each
  offer carries both ``price`` and ``oldPrice``, which is the only place the
  discount is available.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)
_PATCH_RE = re.compile(
    r'<noframes data-apiary="patch">(.*?)</noframes>',
    re.DOTALL,
)

# New scheme: /card/<slug>/<marketSku>. Legacy: /product[--slug]/<modelId>
# (still resolvable, but modelId lives in a different id space).
PRODUCT_URL_RE = re.compile(
    r'market\.yandex\.ru/(?:card/[^/"\'?#]+/|product(?:--[^/"\'?#]+)?/)'
    r'(\d+)'
)


def build_product_url(product_id: str) -> str:
    # The slug is cosmetic — any non-empty placeholder resolves the page.
    return f'https://market.yandex.ru/card/x/{product_id}'


def iter_ld_json_blocks(html: str) -> Iterator[dict[str, Any]]:
    for raw in _LD_JSON_RE.findall(html):
        raw = raw.strip()
        if not raw:
            continue
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                yield item


def iter_ld_json_products(html: str) -> Iterator[dict[str, Any]]:
    """Yield Product/IndividualProduct nodes, including ones nested inside
    an ItemList's ``itemListElement[].item`` (category/search pages)."""
    for block in iter_ld_json_blocks(html):
        yield from _walk_products(block)


def _walk_products(node: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 4 or not isinstance(node, (dict, list)):
        return
    if isinstance(node, dict):
        if node.get('@type') in ('Product', 'IndividualProduct'):
            yield node
        for value in node.values():
            yield from _walk_products(value, depth + 1)
    else:
        for item in node:
            yield from _walk_products(item, depth + 1)


def product_id_from_ld_json(item: dict[str, Any]) -> str | None:
    # The URL-embedded id (oskuId) is the actual routing key for
    # /card/<slug>/<id> — item['sku'] is a different, non-routable id
    # (marketSku) that only coincides with it for some offers.
    for key in ('url', '@id'):
        raw = item.get(key)
        if isinstance(raw, str):
            match = PRODUCT_URL_RE.search(raw)
            if match:
                return match.group(1)
    sku = item.get('sku')
    if isinstance(sku, (str, int)) and str(sku).isdigit():
        return str(sku)
    return None


def extract_offer_prices(
    html: str,
    product_id: str,
) -> tuple[Decimal | None, Decimal | None]:
    """Best-effort (price, old_price) lookup from the apiary patch state."""
    best: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None

    for raw in _PATCH_RE.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        offers = data.get('collections', {}).get('offerAnalytics')
        if not isinstance(offers, dict):
            continue
        for entry in offers.values():
            if not isinstance(entry, dict):
                continue
            if fallback is None:
                fallback = entry
            candidates = {
                str(entry.get(key))
                for key in ('oskuId', 'marketSku', 'skuId')
                if entry.get(key) is not None
            }
            if product_id in candidates:
                best = entry
                break
        if best is not None:
            break

    # A product page normally carries exactly one relevant offer, so an
    # id mismatch (different sku scheme) still resolves correctly here.
    entry = best or fallback
    if entry is None:
        return None, None
    return _parse_price(entry.get('price')), _parse_price(entry.get('oldPrice'))


_PRICE_CLEAN_RE = re.compile(r'[^\d.,]')


def _parse_price(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if not isinstance(raw, str):
        return None
    cleaned = _PRICE_CLEAN_RE.sub('', raw).replace(',', '.')
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
