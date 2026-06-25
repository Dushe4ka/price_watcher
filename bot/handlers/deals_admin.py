import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.deals_scheduler import run_deals_pipeline
from src.core.config import settings
from src.crud.posted_deal import posted_deal_crud
from src.database.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def deals_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    async with AsyncSessionLocal() as session:
        total = await posted_deal_crud.count_all(session)
        recent = await posted_deal_crud.get_recent(session, limit=5)
    lines = [
        '📊 <b>Статус Deal Channel Bot</b>',
        f'Канал: <code>{settings.telegram_channel_id or "не задан"}</code>',
        f'Скидка (парсер): {settings.effective_min_parser_discount}%',
        f'Скидка (база): {settings.min_database_discount_percent}%',
        f'Прогрев: {settings.data_collection_warmup_days} дн.',
        f'Хранение цен: {settings.price_history_retention_days} дн.',
        f'Админ: <code>{settings.admin_telegram_id or "не задан"}</code>',
        f'Интервал: {settings.crawl_interval_minutes} мин',
        f'Опубликовано всего: {total}',
        '',
        '<b>Последние посты:</b>',
    ]
    if not recent:
        lines.append('Пока нет опубликованных сделок.')
    else:
        for deal in recent:
            lines.append(
                f'• [{deal.marketplace}] {deal.title[:40]}… '
                f'(-{deal.discount_percent}%)'
            )
    await update.message.reply_text(
        '\n'.join(lines),
        parse_mode='HTML',
    )


async def force_crawl(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        '⏳ Запускаю обход категорий…',
    )
    try:
        stats = await run_deals_pipeline(context.application)
    except Exception as exc:
        logger.exception('Force crawl failed: %s', exc)
        await update.message.reply_text(
            f'❌ Ошибка при обходе: {exc}',
        )
        return
    await update.message.reply_text(
        '✅ Обход завершён\n'
        f'Найдено: {stats.crawled}\n'
        f'Распарсено: {stats.parsed}\n'
        f'Цен в базе: {stats.prices_saved}\n'
        f'Со скидкой: {stats.matched_discount}\n'
        f'Опубликовано: {stats.posted}\n'
        f'На модерации: {stats.sent_to_moderation}\n'
        f'Ниже порога: {stats.skipped_threshold}\n'
        f'Дубликаты: {stats.skipped_duplicate}\n'
        f'Ошибки: {stats.errors}',
    )


def deals_admin_handlers_installer(application: Application) -> None:
    application.add_handler(CommandHandler('deals_status', deals_status))
    application.add_handler(CommandHandler('force_crawl', force_crawl))
