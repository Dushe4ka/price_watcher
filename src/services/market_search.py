from __future__ import annotations

import asyncio
import json
import logging
import re
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from src.parsers import get_parser
from src.parsers.base import ParsedProduct
from src.parsers.utils import NotFoundError, ParserError, create_http_client

logger = logging.getLogger(__name__)

_ALL_MARKETPLACES = ('wildberries', 'ozon', 'yandex_market')

_WB_SEARCH_URL = (
    'https://search.wb.ru/exactmatch/ru/common/v5/search'
    '?appType=1&curr=rub&dest=-1257786&query={query}'
    '&resultset=catalog&limit={limit}&page=1'
)
_OZON_SEARCH_PATH = '/search/?text={query}&from_global=true'
_OZON_COMPOSER_URLS = (
    'https://api.ozon.ru/composer-api.bx/page/json/v2?url={path}',
    'https://www.ozon.ru/api/composer-api.bx/page/json/v2?url={path}',
)
_OZON_MOBILE_HEADERS = {
    'x-o3-app-name': 'ozonapp_android',
    'x-o3-app-version': '17.35.0',
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 14; SM-S918B) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Mobile Safari/537.36'
    ),
}
_YM_PRODUCT_LINK_RE = re.compile(
    r'href="[^"]*?/product(?:--[^/"]+)?/(\d+)"'
)
_MODEL_RE = re.compile(
    r'\b([A-Za-zА-Яа-яЁё]{2,}[\w\-]*\d[\w\-]*|\d[\w\-]*[A-Za-zА-Яа-яЁё][\w\-]*)\b'
)
_STOP_WORDS = frozenset({
    'для', 'и', 'с', 'на', 'в', 'по', 'из', 'от', 'до', 'без', 'the', 'with',
    'новый', 'новая', 'новое', 'купить', 'цвет', 'размер', 'комплект', 'набор',
    'шт', 'мм', 'см', 'литр', 'л', 'г', 'кг', 'мл', 'год', 'гарантия',
})


def build_search_query(title: str) -> str:
    """Собирает короткий запрос для поиска той же модели на других площадках."""
    cleaned = re.sub(r'\([^)]*\)', ' ', title)
    cleaned = re.sub(r'[«»"\'\[\]]', ' ', cleaned)
    models = _MODEL_RE.findall(cleaned)
    if models:
        unique_models: list[str] = []
        for model in models:
            token = model.strip()
            if len(token) >= 4 and token not in unique_models:
                unique_models.append(token)
        if unique_models:
            return ' '.join(unique_models[:3])[:100]

    tokens: list[str] = []
    for raw in re.split(r'[\s,/|+]+', cleaned):
        word = raw.strip('.,;:-')
        if len(word) < 3:
            continue
        if word.lower() in _STOP_WORDS:
            continue
        if word.isdigit():
            continue
        tokens.append(word)
        if len(tokens) >= 6:
            break
    return ' '.join(tokens)[:100]


def title_matches_query(query: str, title: str) -> bool:
    query_tokens = [
        token.lower()
        for token in re.split(r'[\s,/|+]+', query)
        if len(token) >= 3
    ]
    if not query_tokens:
        return True
    title_lower = title.lower()
    matches = sum(1 for token in query_tokens if token in title_lower)
    required = min(2, len(query_tokens))
    return matches >= required


async def search_product_ids(
    marketplace: str,
    query: str,
    limit: int = 3,
) -> list[str]:
    if marketplace == 'wildberries':
        return await _search_wildberries(query, limit)
    if marketplace == 'ozon':
        return await _search_ozon(query, limit)
    if marketplace == 'yandex_market':
        return await _search_yandex_market(query, limit)
    return []


async def fetch_market_prices(
    product: ParsedProduct,
    source_marketplace: str,
    search_query: str,
    *,
    limit_per_marketplace: int = 3,
) -> tuple[list[Decimal], list[str]]:
    """Ищет похожие товары на других площадках и возвращает их цены."""
    prices: list[Decimal] = []
    marketplaces: list[str] = []

    for marketplace in _ALL_MARKETPLACES:
        if marketplace == source_marketplace:
            continue
        product_ids = await search_product_ids(
            marketplace,
            search_query,
            limit=limit_per_marketplace,
        )
        if not product_ids:
            continue

        parser = get_parser(marketplace)
        found_on_marketplace = False
        for product_id in product_ids:
            try:
                candidate = await parser.parse_product(product_id)
            except (NotFoundError, ParserError) as exc:
                logger.debug(
                    'Market search parse failed %s/%s: %s',
                    marketplace,
                    product_id,
                    exc,
                )
                continue
            if not candidate.in_stock:
                continue
            if not title_matches_query(search_query, candidate.title):
                continue
            prices.append(candidate.price)
            found_on_marketplace = True
            await asyncio.sleep(0.4)

        if found_on_marketplace:
            marketplaces.append(marketplace)

    return prices, marketplaces


async def _search_wildberries(query: str, limit: int) -> list[str]:
    url = _WB_SEARCH_URL.format(query=quote(query), limit=limit)
    async with create_http_client() as client:
        response = await client.get(url)
        if response.status_code != 200:
            return []
        try:
            data: dict[str, Any] = response.json()
        except json.JSONDecodeError:
            return []

    products: list[dict[str, Any]] = data.get('data', {}).get('products', [])
    return [str(item['id']) for item in products if item.get('id')][:limit]


async def _search_ozon(query: str, limit: int) -> list[str]:
    path = _OZON_SEARCH_PATH.format(query=quote(query))
    encoded_path = quote(path, safe='')
    async with create_http_client(headers=_OZON_MOBILE_HEADERS) as client:
        for template in _OZON_COMPOSER_URLS:
            response = await client.get(template.format(path=encoded_path))
            if response.status_code != 200:
                continue
            try:
                payload: dict[str, Any] = response.json()
            except json.JSONDecodeError:
                continue
            product_ids = _extract_ozon_product_ids(payload, limit)
            if product_ids:
                return product_ids
    return []


def _extract_ozon_product_ids(payload: dict[str, Any], limit: int) -> list[str]:
    product_ids: list[str] = []
    product_re = re.compile(r'/product/(?:[^/]+-)?(\d+)')

    for raw_value in payload.get('widgetStates', {}).values():
        if len(product_ids) >= limit:
            break
        text = raw_value if isinstance(raw_value, str) else json.dumps(raw_value)
        for match in product_re.finditer(text):
            product_id = match.group(1)
            if product_id not in product_ids:
                product_ids.append(product_id)
            if len(product_ids) >= limit:
                break
    return product_ids[:limit]


async def _search_yandex_market(query: str, limit: int) -> list[str]:
    url = f'https://market.yandex.ru/search?text={quote(query)}'
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9',
    }
    async with create_http_client(headers=headers) as client:
        response = await client.get(url)
        if response.status_code != 200:
            return []
        html = response.text

    product_ids: list[str] = []
    for match in _YM_PRODUCT_LINK_RE.finditer(html):
        product_id = match.group(1)
        if product_id not in product_ids:
            product_ids.append(product_id)
        if len(product_ids) >= limit:
            break
    return product_ids
