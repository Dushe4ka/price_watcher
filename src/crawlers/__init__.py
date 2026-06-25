"""Краулеры категорий маркетплейсов."""

from src.crawlers.base import CategoryCrawlResult, MarketplaceCrawler
from src.crawlers.ozon import OzonCategoryCrawler
from src.crawlers.wildberries import WildberriesCategoryCrawler
from src.crawlers.yandex_market import YandexMarketCategoryCrawler

CRAWLERS: dict[str, MarketplaceCrawler] = {
    'ozon': OzonCategoryCrawler(),
    'wildberries': WildberriesCategoryCrawler(),
    'yandex_market': YandexMarketCategoryCrawler(),
}


def get_crawler(marketplace: str) -> MarketplaceCrawler:
    crawler = CRAWLERS.get(marketplace)
    if crawler is None:
        raise ValueError(f'Unknown marketplace crawler: {marketplace}')
    return crawler


__all__ = [
    'CategoryCrawlResult',
    'MarketplaceCrawler',
    'CRAWLERS',
    'get_crawler',
]
