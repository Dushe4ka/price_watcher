"""Live smoke: Ozon category crawl must return N>0 products with prices.

Uses Playwright entrypoint/composer path. Exit 0 on success, 2 on empty/blocked,
1 on hard failure.

Respects anti-bot: polite delay, no hammering. Prefer PROXY_LIST (RU residential)
when the egress IP is flagged by Ozon antibot.

Run:
  python -m scripts.smoke_ozon_crawl
  OZON_SMOKE_LIMIT=5 python -m scripts.smoke_ozon_crawl
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from src.crawlers.ozon import OzonCategoryCrawler
from src.ozon.client import ozon_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('smoke_ozon_crawl')


async def main() -> int:
    limit = int(os.getenv('OZON_SMOKE_LIMIT', '5'))
    crawler = OzonCategoryCrawler()
    try:
        result = await crawler.crawl_category(
            crawl_url='/category/krasota-i-zdorove-6500/',
            category_slug='beauty',
            limit=limit,
        )
    finally:
        await ozon_client.close()

    priced = [
        pid
        for pid in result.product_ids
        if result.pre_parsed.get(pid)
        and result.pre_parsed[pid].price > 0
    ]
    logger.info(
        'Ozon smoke: product_ids=%s priced=%s',
        len(result.product_ids),
        len(priced),
    )
    for pid in priced[:5]:
        p = result.pre_parsed[pid]
        logger.info(
            '  %s | %s | %s RUB | disc=%s',
            pid,
            (p.title or '')[:60],
            p.price,
            p.discount_percent,
        )

    if not priced:
        logger.error(
            'FAIL: 0 priced products from Ozon crawl '
            '(check antibot / set PROXY_LIST with RU residential)'
        )
        return 2
    logger.info('OK: N=%s priced products', len(priced))
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
