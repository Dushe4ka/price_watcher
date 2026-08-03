from __future__ import annotations

import re

OZON_COMPOSER_URLS = (
    'https://api.ozon.ru/composer-api.bx/page/json/v2?url={path}',
    'https://www.ozon.ru/api/composer-api.bx/page/json/v2?url={path}',
)

OZON_SEARCH_PATH = '/search/?text={query}&from_global=true'

OZON_MOBILE_HEADERS: dict[str, str] = {
    'x-o3-app-name': 'ozonapp_android',
    'x-o3-app-version': '17.35.0',
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 14; SM-S918B) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Mobile Safari/537.36'
    ),
}

OZON_PRODUCT_RE = re.compile(r'/product/(?:[^/]+-)?(\d+)')
