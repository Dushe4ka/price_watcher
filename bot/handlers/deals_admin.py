import asyncio
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
from src.schemas.deal import DealRunStats

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

    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    if context.bot_data.get('_crawl_running'):
        interaction = await get_interaction(update)
        await send_tracked_message(
            interaction, context,
            text='⏳ Обход уже запущен, дождитесь завершения.',
            reply_markup=Keyboards.admin_back(),
        )
        return

    interaction = await get_interaction(update)
    await send_tracked_message(
        interaction, context,
        text=texts.FORCE_CRAWL_START,
    )

    asyncio.create_task(
        _run_crawl_background(context.application, chat_id),
    )


async def _run_crawl_background(
    application, chat_id: int,
) -> None:
    application.bot_data['_crawl_running'] = True
    try:
        stats = await run_deals_pipeline(application)
        text = _format_crawl_report(stats)
    except Exception as exc:
        logger.exception('Force crawl failed: %s', exc)
        text = f'❌ Ошибка при обходе:\n<code>{exc}</code>'
    finally:
        application.bot_data['_crawl_running'] = False

    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='HTML',
            reply_markup=Keyboards.admin_back(),
        )
    except Exception as exc:
        logger.warning('Failed to send crawl report: %s', exc)


_MP_ICONS = {
    'wildberries': '🟣',
    'ozon': '🔵',
    'yandex_market': '🟡',
}
_MP_NAMES = {
    'wildberries': 'Wildberries',
    'ozon': 'Ozon',
    'yandex_market': 'Яндекс Маркет',
}


def _format_crawl_report(stats: DealRunStats) -> str:
    ok = stats.errors == 0
    header = '✅ <b>Обход завершён</b>' if ok else '⚠️ <b>Обход завершён с ошибками</b>'

    lines = [header, '']

    if stats.per_marketplace:
        for mp, ms in sorted(stats.per_marketplace.items()):
            icon = _MP_ICONS.get(mp, '⚪')
            name = _MP_NAMES.get(mp, mp)
            parts = [f'{ms.crawled} найдено']
            if ms.parsed:
                parts.append(f'{ms.parsed} распаршено')
            if ms.posted:
                parts.append(f'{ms.posted} опубл.')
            if ms.errors:
                parts.append(f'{ms.errors} ош.')
            lines.append(f'{icon} <b>{name}</b>: {" · ".join(parts)}')
        lines.append('')

    lines.append(f'📦 Найдено: <b>{stats.crawled}</b>  →  распаршено: <b>{stats.parsed}</b>')
    lines.append(f'💾 Цен сохранено: {stats.prices_saved}')
    lines.append('')

    if stats.matched_discount or stats.posted or stats.sent_to_moderation:
        lines.append(f'🏷 Со скидкой: <b>{stats.matched_discount}</b>')
        if stats.posted:
            lines.append(f'📢 Опубликовано: <b>{stats.posted}</b>')
        if stats.sent_to_moderation:
            lines.append(f'👀 На модерации: {stats.sent_to_moderation}')
        lines.append('')

    skip_parts = []
    if stats.skipped_threshold:
        skip_parts.append(f'порог: {stats.skipped_threshold}')
    if stats.skipped_low_rating:
        skip_parts.append(f'рейтинг: {stats.skipped_low_rating}')
    if stats.skipped_market_check:
        skip_parts.append(f'рынок: {stats.skipped_market_check}')
    if stats.skipped_duplicate:
        skip_parts.append(f'дубли: {stats.skipped_duplicate}')
    if skip_parts:
        lines.append(f'⏭ Пропущено: {" · ".join(skip_parts)}')

    if stats.errors:
        lines.append(f'❌ Ошибки: {stats.errors}')

    return '\n'.join(lines)


def deals_admin_handlers_installer(application: Application) -> None:
    application.add_handler(CommandHandler('deals_status', deals_status))
    application.add_handler(CommandHandler('force_crawl', force_crawl))
    application.add_handler(
        CallbackQueryHandler(deals_status, pattern=f'^{ADMIN_DEALS_STATUS}$'),
    )
    application.add_handler(
        CallbackQueryHandler(force_crawl, pattern=f'^{ADMIN_FORCE_CRAWL}$'),
    )
