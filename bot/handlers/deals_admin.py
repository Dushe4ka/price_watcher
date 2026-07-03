import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from bot.deals_scheduler import run_deals_pipeline
from bot.handlers.callback_data import ADMIN_DEALS_STATUS, ADMIN_FORCE_CRAWL, ADMIN_PANEL
from bot.handlers.pre_process import clear_messages
from bot.handlers.utils import get_interaction, send_tracked_message
from bot.navigation import copy as texts
from bot.navigation.state import is_admin
from bot.navigation.keyboards import Keyboards
from src.core.config import settings
from src.crud.posted_deal import posted_deal_crud
from src.database.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _admin_guard(update: Update) -> bool:
    user = update.effective_user
    return user is not None and is_admin(user.id)


async def _reply_admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    interaction = await get_interaction(update)
    await send_tracked_message(
        interaction,
        context,
        text=texts.ADMIN_ONLY,
        reply_markup=Keyboards.help_screen(),
    )


async def deals_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _admin_guard(update):
        await _reply_admin_only(update, context)
        return

    async with AsyncSessionLocal() as session:
        total = await posted_deal_crud.count_all(session)
        recent = await posted_deal_crud.get_recent(session, limit=5)
    lines = [
        '📊 <b>Статус канала со скидками</b>',
        '━━━━━━━━━━━━━━━━━━━━',
        f'Канал: <code>{settings.telegram_channel_id or "не задан"}</code>',
        f'Скидка (с сайта): {settings.effective_min_parser_discount}%',
        f'Скидка (по истории): {settings.min_database_discount_percent}%',
        f'Проверка рынка: от {settings.market_check_min_price} ₽',
        f'Интервал обхода: {settings.crawl_interval_minutes} мин',
        f'Опубликовано всего: <b>{total}</b>',
        '',
        '<b>Последние посты:</b>',
    ]
    if not recent:
        lines.append('<i>Пока нет опубликованных сделок.</i>')
    else:
        for deal in recent:
            lines.append(
                f'• [{deal.marketplace}] {deal.title[:40]}… '
                f'(-{deal.discount_percent}%)',
            )

    interaction = await get_interaction(update)
    await send_tracked_message(
        interaction,
        context,
        text='\n'.join(lines),
        reply_markup=Keyboards.admin_back(),
    )


async def force_crawl(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _admin_guard(update):
        await _reply_admin_only(update, context)
        return

    interaction = await get_interaction(update)
    await send_tracked_message(
        interaction,
        context,
        text=texts.FORCE_CRAWL_START,
    )
    try:
        stats = await run_deals_pipeline(context.application)
    except Exception as exc:
        logger.exception('Force crawl failed: %s', exc)
        await send_tracked_message(
            interaction,
            context,
            text=f'❌ Ошибка при обходе:\n<code>{exc}</code>',
            reply_markup=Keyboards.admin_back(),
        )
        return
    await send_tracked_message(
        interaction,
        context,
        text=(
            '✅ <b>Обход завершён</b>\n'
            '━━━━━━━━━━━━━━━━━━━━\n'
            f'Найдено: {stats.crawled}\n'
            f'Распарсено: {stats.parsed}\n'
            f'Цен в базе: {stats.prices_saved}\n'
            f'Со скидкой: {stats.matched_discount}\n'
            f'Опубликовано: {stats.posted}\n'
            f'На модерации: {stats.sent_to_moderation}\n'
            f'Ниже порога: {stats.skipped_threshold}\n'
            f'Не дешевле рынка: {stats.skipped_market_check}\n'
            f'Дубликаты: {stats.skipped_duplicate}\n'
            f'Ошибки: {stats.errors}'
        ),
        reply_markup=Keyboards.admin_back(),
    )


def deals_admin_handlers_installer(application: Application) -> None:
    application.add_handler(CommandHandler('deals_status', deals_status))
    application.add_handler(CommandHandler('force_crawl', force_crawl))
    application.add_handler(
        CallbackQueryHandler(deals_status, pattern=f'^{ADMIN_DEALS_STATUS}$'),
    )
    application.add_handler(
        CallbackQueryHandler(force_crawl, pattern=f'^{ADMIN_FORCE_CRAWL}$'),
    )
