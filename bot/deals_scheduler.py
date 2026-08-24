import logging

from telegram.ext import Application

from bot.commands import setup_bot_commands
from src.core.config import settings
from src.database.db import AsyncSessionLocal
from src.marketplaces.service import (
    close_marketplace_services,
    configure_marketplace_runtime,
)
from src.schemas.deal import DealRunStats
from src.services.deal_pipeline import DealPipeline

logger = logging.getLogger(__name__)

CRAWL_JOB_ID = 'deals_crawl_job'


async def run_deals_pipeline(application: Application) -> DealRunStats:
    bot = application.bot
    pipeline = DealPipeline(bot=bot)
    async with AsyncSessionLocal() as session:
        stats = await pipeline.run(session)
    logger.info(
        'Deals pipeline finished: crawled=%s parsed=%s saved=%s matched=%s '
        'posted=%s moderation=%s skipped=%s market=%s duplicates=%s '
        'errors=%s challenges=%s fallbacks=%s sources=%s',
        stats.crawled,
        stats.parsed,
        stats.prices_saved,
        stats.matched_discount,
        stats.posted,
        stats.sent_to_moderation,
        stats.skipped_threshold,
        stats.skipped_market_check,
        stats.skipped_duplicate,
        stats.errors,
        stats.challenges,
        stats.fallback_activations,
        stats.source_outcomes,
    )
    return stats


async def bot_post_init(application: Application) -> None:
    configure_marketplace_runtime('bot')
    await setup_bot_commands(application)
    await start_deals_scheduler(application)


async def bot_post_shutdown(application: Application) -> None:
    """Stop the scheduler and release marketplace resources exactly once."""
    scheduler = application.bot_data.pop('deals_scheduler', None)
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning('Deals scheduler shutdown failed: %s', exc)
    await close_marketplace_services()


async def start_deals_scheduler(application: Application) -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    if not settings.deals_enabled:
        logger.info('DEALS_ENABLED=false, scheduler not started')
        return

    scheduler = AsyncIOScheduler()

    async def job_callback() -> None:
        await run_deals_pipeline(application)

    scheduler.add_job(
        job_callback,
        trigger='interval',
        minutes=settings.crawl_interval_minutes,
        id=CRAWL_JOB_ID,
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    application.bot_data['deals_scheduler'] = scheduler
    logger.info(
        'Deals scheduler started: every %s minutes',
        settings.crawl_interval_minutes,
    )
