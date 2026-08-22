"""Code-owned marketplace URLs and strict navigation validation."""

from __future__ import annotations

import ipaddress
from urllib.parse import quote, urlencode, urlsplit

from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    ProductRequest,
    SearchRequest,
)


_ALLOWED_HOSTS: dict[MarketplaceName, frozenset[str]] = {
    'ozon': frozenset({'www.ozon.ru'}),
    'wildberries': frozenset({'www.wildberries.ru'}),
    'yandex_market': frozenset({'market.yandex.ru'}),
}


class UnsafeMarketplaceUrl(ValueError):
    """A navigation target is outside the exact marketplace allowlist."""


class CategoryUrlResolutionRequired(ValueError):
    """Category navigation must come from trusted monitored configuration."""


def build_marketplace_url(
    marketplace: MarketplaceName,
    request: ProductRequest | SearchRequest | CategoryRequest,
) -> str:
    """Build safe product/search navigation from a typed request.

    Category slugs do not have one stable cross-marketplace mapping. Task 12
    resolves them through trusted monitored configuration, then calls
    :func:`validate_main_frame_url` on the exact configured URL.
    """
    if marketplace not in _ALLOWED_HOSTS:
        raise UnsafeMarketplaceUrl('unsupported marketplace')
    if isinstance(request, CategoryRequest):
        raise CategoryUrlResolutionRequired(
            'category URL requires trusted configuration resolution',
        )
    if isinstance(request, ProductRequest):
        url = _build_product_url(marketplace, request.product_id)
    elif isinstance(request, SearchRequest):
        url = _build_search_url(marketplace, request)
    else:
        raise TypeError('unsupported marketplace request')
    return validate_main_frame_url(marketplace, url)


def validate_main_frame_url(
    marketplace: MarketplaceName,
    url: str,
) -> str:
    """Return a URL only when every navigation boundary is safe."""
    allowed_hosts = _ALLOWED_HOSTS.get(marketplace)
    if allowed_hosts is None:
        raise UnsafeMarketplaceUrl('unsupported marketplace')
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = parsed.hostname
    except (TypeError, ValueError):
        raise UnsafeMarketplaceUrl('malformed marketplace URL') from None
    if parsed.scheme != 'https':
        raise UnsafeMarketplaceUrl('marketplace URL must use HTTPS')
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeMarketplaceUrl(
            'marketplace URL must not contain userinfo',
        )
    if host is None or host not in allowed_hosts:
        raise UnsafeMarketplaceUrl('marketplace URL host is not allowed')
    if _is_ip_literal(host):
        raise UnsafeMarketplaceUrl(
            'marketplace URL must not use an IP literal',
        )
    if port not in (None, 443):
        raise UnsafeMarketplaceUrl('marketplace URL must use the default port')
    return url


def _build_product_url(
    marketplace: MarketplaceName,
    product_id: str,
) -> str:
    encoded_id = _encode_required_value(product_id, 'product ID')
    templates = {
        'ozon': 'https://www.ozon.ru/product/{}/',
        'wildberries': (
            'https://www.wildberries.ru/catalog/{}/detail.aspx'
        ),
        'yandex_market': 'https://market.yandex.ru/card/x/{}',
    }
    return templates[marketplace].format(encoded_id)


def _build_search_url(
    marketplace: MarketplaceName,
    request: SearchRequest,
) -> str:
    query = request.query.strip()
    if not query:
        raise UnsafeMarketplaceUrl('search query must not be empty')
    parameters: dict[str, str | int]
    if marketplace == 'ozon':
        base_url = 'https://www.ozon.ru/search/'
        parameters = {'text': query, 'from_global': 'true'}
    elif marketplace == 'wildberries':
        base_url = 'https://www.wildberries.ru/catalog/0/search.aspx'
        parameters = {'search': query}
    else:
        base_url = 'https://market.yandex.ru/search'
        parameters = {'text': query}
    if request.page > 1:
        parameters['page'] = request.page
    return f'{base_url}?{urlencode(parameters)}'


def _encode_required_value(value: str, label: str) -> str:
    if not value:
        raise UnsafeMarketplaceUrl(f'{label} must not be empty')
    return quote(value, safe='')


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
