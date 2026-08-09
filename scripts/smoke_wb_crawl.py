"""Live smoke: WB category crawl must return N>0 products with prices.

Respects WB rate limits. Exit 0 on success, 2 on empty, 1 on hard failure.

Run:
  python -m scripts.smoke_wb_crawl
  WB_SMOKE_LIMIT=5 python -m scripts.smoke_wb_crawl
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from src.crawlers.wildberries import WildberriesCategoryCrawler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('smoke_wb_crawl')


async def main() -> int:
    limit = int(os.getenv('WB_SMOKE_LIMIT', '5'))
    crawler = WildberriesCategoryCrawler()
    result = await crawler.crawl_category(
        crawl_url='https://www.wildberries.ru/catalog/krasota/aptechnaya-kosmetika',
        category_slug='beauty',
        limit=limit,
    )
    priced = [
        pid
        for pid in result.product_ids
        if result.pre_parsed.get(pid)
        and result.pre_parsed[pid].price > 0
    ]
    logger.info(
        'WB smoke: product_ids=%s priced=%s',
        len(result.product_ids),
        len(priced),
    )
    for pid in priced[:5]:
        p = result.pre_parsed[pid]
        logger.info(
            '  %s | %s | %s RUB | disc=%s',
            pid,
            p.title[:60],
            p.price,
            p.discount_percent,
        )

    if not priced:
        logger.error('FAIL: 0 priced products from WB crawl')
        return 2
    logger.info('OK: N=%s priced products', len(priced))
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
