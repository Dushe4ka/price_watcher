from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode

from src.browser.allowlist import UnsafeMarketplaceUrl, build_marketplace_url
from src.core.config import settings
from src.crawlers.base import crawl_category_result
from src.crud.deal_moderation import deal_moderation_crud
from src.crud.posted_deal import posted_deal_crud
from src.crud.price_tracking import (
    product_price_history_crud,
    tracked_product_crud,
)
from src.database.enums import ModerationStatus
from src.marketplaces.contracts import ProductRequest, SourceOutcome
from src.marketplaces.diagnostics import (
    accumulate_marketplace_diagnostics,
    summarize_attempts,
)
from src.parsers.base import ParsedProduct, parse_product_result
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


def _product_url(marketplace: str, product_id: str) -> str | None:
    """Build a code-owned product URL for an allowlisted marketplace."""
    try:
        return build_marketplace_url(marketplace, ProductRequest(product_id))
    except (UnsafeMarketplaceUrl, TypeError, ValueError):
        return None


class DealPipeline:
    def __init__(self, bot: Bot | None = None) -> None:
        self._bot = bot
        self._evaluator = DiscountEvaluator()
        self._market_checker = MarketPriceChecker()

    async def _send_photo_or_message(
        self,
        *,
        chat_id: int,
        text: str,
        image_url: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        if image_url:
            try:
                return await self._bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            except Exception as exc:
                logger.warning(
                    'Photo send failed for chat %s, fallback to text: %s',
                    chat_id,
                    exc,
                )
        return await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=False,
        )

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
        category_slug: str,
        hashtag: str,
    ) -> None:
        crawl = await crawl_category_result(
            marketplace,
            category_slug,
            limit=settings.max_products_per_category,
        )
        accumulate_marketplace_diagnostics(stats, crawl)
        if crawl.outcome is not SourceOutcome.SUCCESS or crawl.value is None:
            logger.info(
                'Category crawl unusable: %s',
                summarize_attempts(crawl),
            )
            return
        crawl_result = crawl.value
        mp_stats = stats.mp(marketplace)
        n_crawled = len(crawl_result.product_ids)
        stats.crawled += n_crawled
        mp_stats.crawled += n_crawled

        for product_id in crawl_result.product_ids:
            pre = crawl_result.pre_parsed.get(product_id)
            if pre is not None:
                product = pre
                stats.parsed += 1
                mp_stats.parsed += 1
            else:
                parsed = await parse_product_result(marketplace, product_id)
                accumulate_marketplace_diagnostics(stats, parsed)
                if (
                    parsed.outcome is not SourceOutcome.SUCCESS
                    or parsed.value is None
                ):
                    logger.info(
                        'Product parse unusable: %s',
                        summarize_attempts(parsed),
                    )
                    continue
                product = parsed.value
                stats.parsed += 1
                mp_stats.parsed += 1
                await asyncio.sleep(_PRODUCT_DELAY_SEC)
            if not product.in_stock:
                continue

            min_rating = settings.min_product_rating
            if min_rating > 0:
                if settings.require_rating and product.rating is None:
                    stats.skipped_low_rating += 1
                    continue
                if product.rating is not None and product.rating < min_rating:
                    stats.skipped_low_rating += 1
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
                    product_url=_product_url(marketplace, product_id),
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
            mp_stats.posted += 1

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
        rows = []
        if product.product_url:
            rows.append([InlineKeyboardButton(
                '🛒 Перейти к товару',
                url=product.product_url,
            )])
        rows.append([
            InlineKeyboardButton(
                '✅ Принять',
                callback_data=f'{MODERATION_APPROVE_PREFIX}{moderation.id}',
            ),
            InlineKeyboardButton(
                '❌ Отклонить',
                callback_data=f'{MODERATION_REJECT_PREFIX}{moderation.id}',
            ),
        ])
        keyboard = InlineKeyboardMarkup(rows)

        try:
            first_message_id: int | None = None
            for admin_id in settings.admin_telegram_id_list:
                try:
                    message = await self._send_photo_or_message(
                        chat_id=admin_id,
                        text=caption,
                        image_url=product.image_url,
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

        post = format_deal_post(
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
            message = await self._send_photo_or_message(
                chat_id=channel_id,
                text=post.text,
                image_url=product.image_url,
                reply_markup=post.reply_markup,
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
