from __future__ import annotations

import re

# Prefer entrypoint-api: after antibot (abt_att) it often returns 200 while
# composer-api stays 403 from the same session.
OZON_PAGE_JSON_URLS = (
    'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url={path}',
    'https://www.ozon.ru/api/composer-api.bx/page/json/v2?url={path}',
    'https://api.ozon.ru/composer-api.bx/page/json/v2?url={path}',
)

# Back-compat alias used by older imports/tests.
OZON_COMPOSER_URLS = OZON_PAGE_JSON_URLS

OZON_SEARCH_PATH = '/search/?text={query}&from_global=true'

OZON_HOME_URL = 'https://www.ozon.ru/'

OZON_DESKTOP_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

OZON_MOBILE_HEADERS: dict[str, str] = {
    'x-o3-app-name': 'ozonapp_android',
    'x-o3-app-version': '17.35.0',
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 14; SM-S918B) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Mobile Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}

OZON_PRODUCT_RE = re.compile(r'/product/(?:[^/]+-)?(\d+)')

OZON_CHALLENGE_TITLE_MARKERS = (
    'antibot challenge',
    'нет соединения',
    'нет\xa0соединения',
)
