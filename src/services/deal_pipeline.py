from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from src.core.config import settings
from src.crawlers import get_crawler
from src.crud.deal_moderation import deal_moderation_crud
from src.crud.posted_deal import posted_deal_crud
from src.crud.price_tracking import (
    product_price_history_crud,
    tracked_product_crud,
)
from src.database.enums import ModerationStatus
from src.parsers import get_parser
from src.parsers.base import ParsedProduct
from src.parsers.utils import NotFoundError, ParserError
from src.schemas.deal import DealModerationCreate, DealRunStats, PostedDealCreate
from src.services.categories_loader import load_categories_config
from src.services.discount_evaluator import DealAction, DiscountEvaluator
from src.services.market_price_checker import MarketPriceChecker
from src.services.post_formatter import format_deal_post, format_moderation_request

logger = logging.getLogger(__name__)

_CATEGORY_DELAY_SEC = 1.5
_PRODUCT_DELAY_SEC = 0.8

MODERATION_APPROVE_PREFIX = 'deal_mod:approve:'
MODERATION_REJECT_PREFIX = 'deal_mod:reject:'


class DealPipeline:
    def __init__(self, bot: Bot | None = None) -> None:
        self._bot = bot
        self._evaluator = DiscountEvaluator()
        self._market_checker = MarketPriceChecker()

    async def run(self, session: AsyncSession) -> DealRunStats:
        stats = DealRunStats()
        deleted = await product_price_history_crud.delete_older_than(session)
        if deleted:
            logger.info('Deleted %s old price history records', deleted)

        config = load_categories_config()
        if not config.categories:
            logger.warning('No categories configured in YAML')
            return stats

        for category in config.categories:
            for mp_config in category.marketplaces:
                try:
                    await self._process_marketplace_category(
                        session=session,
                        stats=stats,
                        marketplace=mp_config.marketplace,
                        crawl_url=mp_config.crawl_url,
                        category_slug=category.slug,
                        hashtag=category.hashtag,
                    )
                except Exception as exc:
                    stats.errors += 1
                    logger.warning(
                        'Category crawl failed %s/%s: %s',
                        category.slug,
                        mp_config.marketplace,
                        exc,
                    )
                await asyncio.sleep(_CATEGORY_DELAY_SEC)
        return stats

    async def _process_marketplace_category(
        self,
        session: AsyncSession,
        stats: DealRunStats,
        marketplace: str,
        crawl_url: str,
        category_slug: str,
        hashtag: str,
    ) -> None:
        crawler = get_crawler(marketplace)
        parser = get_parser(marketplace)
        crawl_result = await crawler.crawl_category(
            crawl_url=crawl_url,
            category_slug=category_slug,
            limit=settings.max_products_per_category,
        )
        stats.crawled += len(crawl_result.product_ids)

        for product_id in crawl_result.product_ids:
            try:
                product = await parser.parse_product(product_id)
                stats.parsed += 1
            except NotFoundError:
                stats.errors += 1
                continue
            except ParserError as exc:
                stats.errors += 1
                logger.warning(
                    'Parse error %s/%s: %s',
                    marketplace,
                    product_id,
                    exc,
                )
                continue

            await asyncio.sleep(_PRODUCT_DELAY_SEC)
            if not product.in_stock:
                continue

            if not product.product_url:
                product = ParsedProduct(
                    external_id=product.external_id,
                    title=product.title,
                    price=product.price,
                    original_price=product.original_price,
                    discount_percent=product.discount_percent,
                    in_stock=product.in_stock,
                    image_url=product.image_url,
                    product_url=parser.build_url(product_id),
                )

            tracked = await tracked_product_crud.get_or_create(
                session,
                product,
                marketplace,
                category_slug,
            )
            parser_discount = self._evaluator.calc_parser_discount(product)
            await product_price_history_crud.add_record(
                session,
                tracked_product_id=tracked.id,
                price=product.price,
                parser_original_price=product.original_price,
                parser_discount_percent=parser_discount,
            )
            stats.prices_saved += 1

            average_price = await product_price_history_crud.get_average_price(
                session,
                tracked.id,
            )
            decision = await self._evaluator.evaluate(
                session,
                product,
                average_price,
            )

            if decision.action != DealAction.SKIP:
                market_result = await self._market_checker.check(
                    product,
                    marketplace,
                    category_slug,
                )
                decision = DiscountEvaluator.apply_market_check(
                    decision,
                    market_result,
                )

            if decision.action == DealAction.SKIP:
                stats.skipped_threshold += 1
                if decision.reason == 'not_cheaper_than_market':
                    stats.skipped_market_check += 1
                await self._log_moderation(
                    session,
                    tracked.id,
                    product,
                    marketplace,
                    category_slug,
                    hashtag,
                    decision,
                    ModerationStatus.SKIPPED,
                )
                continue

            stats.matched_discount += 1

            if await posted_deal_crud.exists(
                marketplace, product.external_id, session
            ):
                stats.skipped_duplicate += 1
                await self._log_moderation(
                    session,
                    tracked.id,
                    product,
                    marketplace,
                    category_slug,
                    hashtag,
                    decision,
                    ModerationStatus.SKIPPED,
                    reason_override='already_posted',
                )
                continue

            if decision.action == DealAction.MODERATE:
                if await deal_moderation_crud.has_pending_for_product(
                    session,
                    marketplace,
                    product.external_id,
                ):
                    continue
                await self._send_to_moderation(
                    session,
                    tracked.id,
                    product,
                    marketplace,
                    category_slug,
                    hashtag,
                    decision,
                )
                stats.sent_to_moderation += 1
                continue

            show_avg_note = decision.action == DealAction.POST_AVERAGE_NOTE
            display_discount = (
                decision.database_discount
                if show_avg_note and decision.database_discount is not None
                else decision.parser_discount
            )
            message_id = await self._post_to_channel(
                product,
                marketplace,
                hashtag,
                discount_percent=display_discount,
                show_average_price_note=show_avg_note,
                average_price=decision.average_price,
                database_discount_percent=decision.database_discount,
                show_market_note=decision.show_market_note,
                market_min_price=decision.market_min_price,
                market_discount_percent=decision.market_discount_percent,
            )
            if message_id is None:
                stats.errors += 1
                continue

            await posted_deal_crud.create(
                PostedDealCreate(
                    marketplace=marketplace,
                    external_id=product.external_id,
                    category_slug=category_slug,
                    hashtag=hashtag,
                    title=product.title,
                    price=product.price,
                    original_price=product.original_price,
                    discount_percent=display_discount,
                    product_url=product.product_url,
                    image_url=product.image_url,
                    telegram_message_id=message_id,
                ),
                session,
            )
            await self._log_moderation(
                session,
                tracked.id,
                product,
                marketplace,
                category_slug,
                hashtag,
                decision,
                ModerationStatus.AUTO_POSTED,
                channel_message_id=message_id,
            )
            stats.posted += 1

    async def _log_moderation(
        self,
        session: AsyncSession,
        tracked_product_id: int,
        product: ParsedProduct,
        marketplace: str,
        category_slug: str,
        hashtag: str,
        decision,
        status: ModerationStatus,
        *,
        reason_override: str | None = None,
        channel_message_id: int | None = None,
        admin_message_id: int | None = None,
    ) -> None:
        try:
            await deal_moderation_crud.create(
                session,
                DealModerationCreate(
                    tracked_product_id=tracked_product_id,
                    marketplace=marketplace,
                    external_id=product.external_id,
                    category_slug=category_slug,
                    hashtag=hashtag,
                    title=product.title,
                    price=product.price,
                    original_price=product.original_price,
                    average_price=decision.average_price,
                    parser_discount_percent=decision.parser_discount,
                    database_discount_percent=decision.database_discount,
                    market_min_price=decision.market_min_price,
                    market_discount_percent=decision.market_discount_percent,
                    product_url=product.product_url,
                    image_url=product.image_url,
                    status=status.value,
                    decision_reason=reason_override or decision.reason,
                    admin_telegram_id=(
                        settings.admin_telegram_id_list[0]
                        if settings.admin_telegram_id_list
                        else None
                    ),
                    admin_message_id=admin_message_id,
                    channel_message_id=channel_message_id,
                ),
            )
        except Exception as exc:
            await session.rollback()
            logger.warning(
                'Failed to log moderation for %s/%s: %s',
                marketplace,
                product.external_id,
                exc,
            )

    async def _send_to_moderation(
        self,
        session: AsyncSession,
        tracked_product_id: int,
        product: ParsedProduct,
        marketplace: str,
        category_slug: str,
        hashtag: str,
        decision,
    ) -> None:
        if not self._bot or not settings.admin_telegram_id_list:
            logger.warning(
                'Admin not configured, skip moderation for %s',
                product.title,
            )
            await self._log_moderation(
                session,
                tracked_product_id,
                product,
                marketplace,
                category_slug,
                hashtag,
                decision,
                ModerationStatus.SKIPPED,
                reason_override='admin_not_configured',
            )
            return

        moderation = await deal_moderation_crud.create(
            session,
            DealModerationCreate(
                tracked_product_id=tracked_product_id,
                marketplace=marketplace,
                external_id=product.external_id,
                category_slug=category_slug,
                hashtag=hashtag,
                title=product.title,
                price=product.price,
                original_price=product.original_price,
                average_price=decision.average_price,
                parser_discount_percent=decision.parser_discount,
                database_discount_percent=decision.database_discount,
                market_min_price=decision.market_min_price,
                market_discount_percent=decision.market_discount_percent,
                product_url=product.product_url,
                image_url=product.image_url,
                status=ModerationStatus.PENDING.value,
                decision_reason=decision.reason,
                admin_telegram_id=settings.admin_telegram_id_list[0],
            ),
        )

        caption = format_moderation_request(
            product,
            marketplace,
            hashtag,
            parser_discount=decision.parser_discount,
            database_discount=decision.database_discount,
            average_price=decision.average_price,
            market_min_price=decision.market_min_price,
            market_discount_percent=decision.market_discount_percent,
            reason=decision.reason,
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    '✅ Принять',
                    callback_data=f'{MODERATION_APPROVE_PREFIX}{moderation.id}',
                ),
                InlineKeyboardButton(
                    '❌ Отклонить',
                    callback_data=f'{MODERATION_REJECT_PREFIX}{moderation.id}',
                ),
            ],
        ])

        try:
            first_message_id: int | None = None
            for admin_id in settings.admin_telegram_id_list:
                try:
                    if product.image_url:
                        message = await self._bot.send_photo(
                            chat_id=admin_id,
                            photo=product.image_url,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard,
                        )
                    else:
                        message = await self._bot.send_message(
                            chat_id=admin_id,
                            text=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard,
                        )
                    if first_message_id is None:
                        first_message_id = message.message_id
                except Exception as exc:
                    logger.warning(
                        'Failed to send moderation to admin %s: %s',
                        admin_id,
                        exc,
                    )

            if first_message_id is None:
                raise RuntimeError('moderation not delivered to any admin')

            await deal_moderation_crud.update_status(
                session,
                moderation,
                ModerationStatus.PENDING,
                decision.reason,
                admin_message_id=first_message_id,
            )
        except Exception as exc:
            logger.exception('Failed to send moderation request: %s', exc)
            await deal_moderation_crud.update_status(
                session,
                moderation,
                ModerationStatus.SKIPPED,
                f'moderation_send_failed: {exc}',
            )

    async def _post_to_channel(
        self,
        product: ParsedProduct,
        marketplace: str,
        hashtag: str,
        *,
        discount_percent: int | None = None,
        show_average_price_note: bool = False,
        average_price=None,
        database_discount_percent: int | None = None,
        show_market_note: bool = False,
        market_min_price=None,
        market_discount_percent: int | None = None,
    ) -> int | None:
        if not settings.deals_enabled:
            logger.info('Deals disabled, skip post: %s', product.title)
            return 0
        if not self._bot or not settings.telegram_channel_id:
            logger.warning(
                'Channel not configured, skip post: %s',
                product.title,
            )
            return None

        caption = format_deal_post(
            product,
            marketplace,
            hashtag,
            discount_percent=discount_percent,
            show_average_price_note=show_average_price_note,
            average_price=average_price,
            database_discount_percent=database_discount_percent,
            show_market_note=show_market_note,
            market_min_price=market_min_price,
            market_discount_percent=market_discount_percent,
        )
        channel_id = settings.telegram_channel_id
        try:
            if product.image_url:
                message = await self._bot.send_photo(
                    chat_id=channel_id,
                    photo=product.image_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                message = await self._bot.send_message(
                    chat_id=channel_id,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
            return message.message_id
        except Exception as exc:
            logger.exception('Failed to post deal to channel: %s', exc)
            return None

    async def post_approved_moderation(
        self,
        session: AsyncSession,
        moderation_id: int,
    ) -> int | None:
        moderation = await deal_moderation_crud.get(session, moderation_id)
        if moderation is None or moderation.status != ModerationStatus.PENDING:
            return None

        product = ParsedProduct(
            external_id=moderation.external_id,
            title=moderation.title,
            price=moderation.price,
            original_price=moderation.original_price,
            discount_percent=moderation.parser_discount_percent,
            in_stock=True,
            image_url=moderation.image_url,
            product_url=moderation.product_url,
        )
        if await posted_deal_crud.exists(
            moderation.marketplace,
            moderation.external_id,
            session,
        ):
            await deal_moderation_crud.update_status(
                session,
                moderation,
                ModerationStatus.REJECTED,
                'already_posted_before_approval',
            )
            return None

        message_id = await self._post_to_channel(
            product,
            moderation.marketplace,
            moderation.hashtag,
            discount_percent=moderation.parser_discount_percent,
        )
        if message_id is None:
            return None

        await posted_deal_crud.create(
            PostedDealCreate(
                marketplace=moderation.marketplace,
                external_id=moderation.external_id,
                category_slug=moderation.category_slug,
                hashtag=moderation.hashtag,
                title=moderation.title,
                price=moderation.price,
                original_price=moderation.original_price,
                discount_percent=moderation.parser_discount_percent,
                product_url=moderation.product_url,
                image_url=moderation.image_url,
                telegram_message_id=message_id,
            ),
            session,
        )
        await deal_moderation_crud.update_status(
            session,
            moderation,
            ModerationStatus.APPROVED,
            'admin_approved',
            channel_message_id=message_id,
        )
        return message_id
