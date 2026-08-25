"""Standalone loop: crawl every Ozon category on a real interval.

Unlike the bot's deals pipeline, this has no Telegram dependency — it exists
purely to put real, recurring traffic through the persistent Ozon browser
profile (`OZON_PROFILE_DIR`) so it accumulates genuine usage history over
time, without needing a live Telegram bot token on a throwaway test rig.

Run:
  python -m scripts.age_ozon_profile
  OZON_AGE_INTERVAL_MIN=30 python -m scripts.age_ozon_profile
"""

from __future__ import annotations

import asyncio
import logging
import os

from src.crawlers.ozon import OzonCategoryCrawler
from src.marketplaces.telemetry import (
    safe_exception_label,
    silence_transport_request_logs,
)
from src.ozon.client import ozon_client
from src.services.categories_loader import load_categories_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
# ``basicConfig(INFO)`` would otherwise let ``httpx`` log a request line
# carrying the full marketplace URL. This loop runs unattended, forever.
silence_transport_request_logs()
logger = logging.getLogger('age_ozon_profile')


async def crawl_once(crawler: OzonCategoryCrawler) -> None:
    config = load_categories_config()
    for category in config.categories:
        ozon_marketplace = next(
            (mp for mp in category.marketplaces if mp.marketplace == 'ozon'),
            None,
        )
        if ozon_marketplace is None:
            continue
        try:
            result = await crawler.crawl_category(
                crawl_url=ozon_marketplace.crawl_url,
                category_slug=category.slug,
                limit=5,
            )
            priced = sum(
                1
                for pid in result.product_ids
                if result.pre_parsed.get(pid)
                and result.pre_parsed[pid].price > 0
            )
            logger.info(
                'Ozon %s: product_ids=%s priced=%s',
                category.slug,
                len(result.product_ids),
                priced,
            )
        except Exception as exc:
            # Never ``logger.exception`` here: a Playwright launch or proxy
            # failure propagates out of the Ozon session with the proxy URL —
            # credentials included — inside its message and its chained
            # traceback. The failure class plus the category is the most that
            # can be logged safely.
            logger.error(
                'Ozon %s crawl failed (%s)',
                category.slug,
                safe_exception_label(exc),
            )


async def main() -> None:
    interval_min = int(os.getenv('OZON_AGE_INTERVAL_MIN', '30'))
    crawler = OzonCategoryCrawler()
    try:
        while True:
            await crawl_once(crawler)
            logger.info('Sleeping %s minutes until next pass', interval_min)
            await asyncio.sleep(interval_min * 60)
    finally:
        await ozon_client.close()


if __name__ == '__main__':
    asyncio.run(main())
