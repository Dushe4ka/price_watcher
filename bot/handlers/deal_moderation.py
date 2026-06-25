import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from src.core.config import settings
from src.crud.deal_moderation import deal_moderation_crud
from src.database.db import AsyncSessionLocal
from src.database.enums import ModerationStatus
from src.services.deal_pipeline import (
    MODERATION_APPROVE_PREFIX,
    MODERATION_REJECT_PREFIX,
    DealPipeline,
)

logger = logging.getLogger(__name__)


def _is_admin(user_id: int | None) -> bool:
    return bool(
        settings.admin_telegram_id
        and user_id == settings.admin_telegram_id
    )


async def deal_moderation_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    if not _is_admin(query.from_user.id if query.from_user else None):
        await query.answer('Недостаточно прав', show_alert=True)
        return

    data = query.data
    if data.startswith(MODERATION_APPROVE_PREFIX):
        moderation_id = int(data.removeprefix(MODERATION_APPROVE_PREFIX))
        await _handle_approve(query, context, moderation_id)
    elif data.startswith(MODERATION_REJECT_PREFIX):
        moderation_id = int(data.removeprefix(MODERATION_REJECT_PREFIX))
        await _handle_reject(query, moderation_id)


async def _handle_approve(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    moderation_id: int,
) -> None:
    pipeline = DealPipeline(bot=context.application.bot)
    async with AsyncSessionLocal() as session:
        message_id = await pipeline.post_approved_moderation(
            session,
            moderation_id,
        )
        moderation = await deal_moderation_crud.get(session, moderation_id)

    if message_id is None:
        await query.answer('Не удалось опубликовать', show_alert=True)
        if moderation and moderation.status != ModerationStatus.PENDING:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    await query.answer('Опубликовано в канал')
    await query.edit_message_reply_markup(reply_markup=None)
    if query.message:
        await query.message.reply_text(
            f'✅ Опубликовано (msg_id={message_id})',
        )


async def _handle_reject(query, moderation_id: int) -> None:
    async with AsyncSessionLocal() as session:
        moderation = await deal_moderation_crud.get(session, moderation_id)
        if moderation is None or moderation.status != ModerationStatus.PENDING:
            await query.answer('Заявка уже обработана', show_alert=True)
            return
        await deal_moderation_crud.update_status(
            session,
            moderation,
            ModerationStatus.REJECTED,
            'admin_rejected',
        )

    await query.answer('Отклонено')
    await query.edit_message_reply_markup(reply_markup=None)
    if query.message:
        await query.message.reply_text('❌ Публикация отклонена')


def deal_moderation_handlers_installer(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(
            deal_moderation_callback,
            pattern=r'^deal_mod:(approve|reject):\d+$',
        )
    )
