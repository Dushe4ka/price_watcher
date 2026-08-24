"""Краулеры категорий маркетплейсов."""

from __future__ import annotations

from src.crawlers.base import (
    CategoryCrawlResult,
    MarketplaceCrawler,
    crawl_category,
    crawl_category_result,
)

_CRAWLER_CACHE: dict[str, MarketplaceCrawler] = {}


def get_crawler(marketplace: str) -> MarketplaceCrawler:
    cached = _CRAWLER_CACHE.get(marketplace)
    if cached is not None:
        return cached

    if marketplace == 'ozon':
        from src.crawlers.ozon import OzonCategoryCrawler
        crawler: MarketplaceCrawler = OzonCategoryCrawler()
    elif marketplace == 'wildberries':
        from src.crawlers.wildberries import WildberriesCategoryCrawler
        crawler = WildberriesCategoryCrawler()
    elif marketplace == 'yandex_market':
        from src.crawlers.yandex_market import YandexMarketCategoryCrawler
        crawler = YandexMarketCategoryCrawler()
    else:
        raise ValueError(f'Unknown marketplace crawler: {marketplace}')

    _CRAWLER_CACHE[marketplace] = crawler
    return crawler


class _LazyCrawlers:
    """Dict-like registry that imports marketplace crawlers on demand."""

    def get(self, marketplace: str, default=None):
        try:
            return get_crawler(marketplace)
        except ValueError:
            return default

    def __getitem__(self, marketplace: str) -> MarketplaceCrawler:
        return get_crawler(marketplace)

    def __contains__(self, marketplace: object) -> bool:
        return marketplace in ('ozon', 'wildberries', 'yandex_market')


CRAWLERS = _LazyCrawlers()

__all__ = [
    'CategoryCrawlResult',
    'MarketplaceCrawler',
    'CRAWLERS',
    'crawl_category',
    'crawl_category_result',
    'get_crawler',
]
