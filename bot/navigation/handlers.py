from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.pre_process import clear_messages, load_data_for_register_user
from bot.handlers.utils import get_interaction, send_tracked_message
from bot.navigation import copy as texts
from bot.navigation.keyboards import Keyboards
from bot.navigation.state import menu_text


@clear_messages
@load_data_for_register_user
async def cancel_to_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Fallback: выход из диалога в главное меню."""
    interaction = await get_interaction(update)
    user_id = update.effective_user.id if update.effective_user else None
    context.user_data.pop('track_flow', None)
    context.user_data.pop('new_track', None)
    await send_tracked_message(
        interaction,
        context,
        text=texts.CANCEL_TO_MENU,
    )
    await send_tracked_message(
        interaction,
        context,
        text=menu_text(context, user_id),
        reply_markup=Keyboards.main_menu(context, user_id),
    )
    return ConversationHandler.END
