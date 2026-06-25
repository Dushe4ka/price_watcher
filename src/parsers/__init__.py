"""Парсеры карточек товаров с маркетплейсов."""

from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.ozon import OzonParser
from src.parsers.wildberries import WildberriesParser
from src.parsers.yandex_market import YandexMarketParser

PARSERS: dict[str, BaseParser] = {
    'wildberries': WildberriesParser(),
    'ozon': OzonParser(),
    'yandex_market': YandexMarketParser(),
}


def get_parser(marketplace: str) -> BaseParser:
    parser = PARSERS.get(marketplace)
    if parser is None:
        raise ValueError(f'Unknown marketplace: {marketplace}')
    return parser


__all__ = [
    'BaseParser',
    'ParsedProduct',
    'OzonParser',
    'WildberriesParser',
    'YandexMarketParser',
    'PARSERS',
    'get_parser',
]
