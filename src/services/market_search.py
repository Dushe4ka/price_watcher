from __future__ import annotations

import asyncio
import json
import logging
import re
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from src.ozon.client import ozon_client
from src.parsers import get_parser
from src.parsers.base import ParsedProduct
from src.parsers.utils import NotFoundError, ParserError, create_http_client
from src.parsers.wb_api import (
    products_from_search_payload,
    wb_search_headers,
    wb_search_urls,
)

logger = logging.getLogger(__name__)

_ALL_MARKETPLACES = ('wildberries', 'ozon', 'yandex_market')

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
    headers = wb_search_headers(query)
    async with create_http_client() as client:
        for url in wb_search_urls(query, page=1):
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                continue
            try:
                data: dict[str, Any] = response.json()
            except json.JSONDecodeError:
                continue
            products = products_from_search_payload(data)
            ids = [str(item['id']) for item in products if item.get('id')]
            if ids:
                return ids[:limit]
    return []


async def _search_ozon(query: str, limit: int) -> list[str]:
    return await ozon_client.search_product_ids(query, limit)


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
