"""Парсеры карточек товаров с маркетплейсов."""

from src.parsers.base import (
    BaseParser,
    ParsedProduct,
    parse_product,
    parse_product_result,
)

PARSERS: dict[str, BaseParser] = {}


def _build_parser(marketplace: str) -> BaseParser:
    if marketplace == 'wildberries':
        from src.parsers.wildberries import WildberriesParser

        return WildberriesParser()
    if marketplace == 'ozon':
        from src.parsers.ozon import OzonParser

        return OzonParser()
    if marketplace == 'yandex_market':
        from src.parsers.yandex_market import YandexMarketParser

        return YandexMarketParser()
    raise ValueError(f'Unknown marketplace: {marketplace}')


def get_parser(marketplace: str) -> BaseParser:
    parser = PARSERS.get(marketplace)
    if parser is None:
        parser = _build_parser(marketplace)
        PARSERS[marketplace] = parser
    return parser


__all__ = [
    'BaseParser',
    'ParsedProduct',
    'PARSERS',
    'get_parser',
    'parse_product',
    'parse_product_result',
]
