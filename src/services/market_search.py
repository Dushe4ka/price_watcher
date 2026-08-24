from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.marketplaces.contracts import (
    MarketplaceName,
    MarketplaceResult,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.service import get_marketplace_service
from src.parsers.base import ParsedProduct, parse_product_result

logger = logging.getLogger(__name__)

_ALL_MARKETPLACES: tuple[MarketplaceName, ...] = (
    'wildberries',
    'ozon',
    'yandex_market',
)
_CANDIDATE_DELAY_SEC = 0.4

_MODEL_RE = re.compile(
    r'\b([A-Za-zА-Яа-яЁё]{2,}[\w\-]*\d[\w\-]*|\d[\w\-]*[A-Za-zА-Яа-яЁё][\w\-]*)\b'
)
_STOP_WORDS = frozenset({
    'для', 'и', 'с', 'на', 'в', 'по', 'из', 'от', 'до', 'без', 'the', 'with',
    'новый', 'новая', 'новое', 'купить', 'цвет', 'размер', 'комплект', 'набор',
    'шт', 'мм', 'см', 'литр', 'л', 'г', 'кг', 'мл', 'год', 'гарантия',
})


@dataclass(frozen=True, slots=True)
class MarketSearchOutcome:
    """Comparison prices with the source outcomes that produced them."""

    prices: tuple[Decimal, ...]
    marketplaces: tuple[str, ...]
    results: tuple[MarketplaceResult[Any], ...]


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


async def search_products_result(
    marketplace: MarketplaceName,
    query: str,
    limit: int = 3,
    page: int = 1,
) -> MarketplaceResult[tuple[ParsedProduct, ...]]:
    """Search one marketplace over its configured source chain."""
    service = get_marketplace_service(marketplace)
    return await service.search_products(
        SearchRequest(query=query, limit=limit, page=page),
    )


async def search_product_ids(
    marketplace: MarketplaceName,
    query: str,
    limit: int = 3,
) -> list[str]:
    """Unwrap a search into product identifiers, or nothing on failure."""
    result = await search_products_result(marketplace, query, limit)
    if result.outcome is not SourceOutcome.SUCCESS or result.value is None:
        return []
    return [product.external_id for product in result.value]


async def collect_market_prices(
    product: ParsedProduct,
    source_marketplace: str,
    search_query: str,
    *,
    limit_per_marketplace: int = 3,
) -> MarketSearchOutcome:
    """Compare one product against the other marketplaces of the chain."""
    del product
    prices: list[Decimal] = []
    marketplaces: list[str] = []
    results: list[MarketplaceResult[Any]] = []

    for marketplace in _ALL_MARKETPLACES:
        if marketplace == source_marketplace:
            continue
        search = await search_products_result(
            marketplace,
            search_query,
            limit_per_marketplace,
        )
        results.append(search)
        if search.outcome is not SourceOutcome.SUCCESS or not search.value:
            logger.debug(
                'Market search unusable on %s: %s',
                marketplace,
                search.outcome.value,
            )
            continue
        found_on_marketplace = False
        for candidate in search.value:
            parsed = await parse_product_result(
                marketplace,
                candidate.external_id,
            )
            results.append(parsed)
            if (
                parsed.outcome is not SourceOutcome.SUCCESS
                or parsed.value is None
            ):
                continue
            item = parsed.value
            if not item.in_stock:
                continue
            if not title_matches_query(search_query, item.title):
                continue
            prices.append(item.price)
            found_on_marketplace = True
            await asyncio.sleep(_CANDIDATE_DELAY_SEC)

        if found_on_marketplace:
            marketplaces.append(marketplace)

    return MarketSearchOutcome(
        prices=tuple(prices),
        marketplaces=tuple(marketplaces),
        results=tuple(results),
    )


async def fetch_market_prices(
    product: ParsedProduct,
    source_marketplace: str,
    search_query: str,
    *,
    limit_per_marketplace: int = 3,
) -> tuple[list[Decimal], list[str]]:
    """Unwrap comparison prices for call sites that need only the values."""
    outcome = await collect_market_prices(
        product,
        source_marketplace,
        search_query,
        limit_per_marketplace=limit_per_marketplace,
    )
    return list(outcome.prices), list(outcome.marketplaces)
