from telegram import Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, filters)

from bot.handlers.callback_data import (
    ADMIN_PANEL,
    BOT_INFO,
    HELP,
    MENU,
    START_NOTIFICATIONS,
)
from bot.handlers.pre_process import (clear_messages,
                                      load_data_for_register_user)
from bot.handlers.utils import (catch_error, check_authorization,
                                get_interaction, send_tracked_message)
from bot.navigation import copy as texts
from bot.navigation.handlers import cancel_to_menu
from bot.navigation.state import is_admin, menu_text, user_display_name
from bot.navigation.keyboards import Keyboards
from bot.scheduler import (PERIODIC_CHECK_FIRST, PERIODIC_CHECK_INTERVAL,
                           periodic_check)

MESSAGE_HANDLERS = filters.TEXT & ~filters.COMMAND

START_ERROR = 'К сожалению, возникла ошибка при запуске. Попробуйте /start'
START_NOTIFICATIONS_ERROR = 'Не удалось включить уведомления. Попробуйте снова.'


@catch_error(START_ERROR)
@clear_messages
@load_data_for_register_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = user_display_name(update)
    from bot.navigation.state import get_user_state

    state = get_user_state(context)

    if state == 'guest':
        await send_tracked_message(
            update,
            context,
            text=texts.START_GUEST,
            reply_markup=Keyboards.guest_start(),
        )
        return

    await send_tracked_message(
        update,
        context,
        text=texts.START_LOADING,
    )

    if state == 'authorized':
        text = texts.START_AUTHORIZED.format(name=name)
        markup = Keyboards.authorized_start()
    else:
        text = texts.START_REGISTERED_NO_AUTH.format(name=name)
        markup = Keyboards.registered_start()

    await send_tracked_message(
        update,
        context,
        text=text,
        reply_markup=markup,
    )


@clear_messages
@load_data_for_register_user
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interaction = await get_interaction(update)
    user_id = update.effective_user.id
    await send_tracked_message(
        interaction,
        context,
        text=menu_text(context, user_id),
        reply_markup=Keyboards.main_menu(context, user_id),
    )


@clear_messages
async def help_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interaction = await get_interaction(update)
    await send_tracked_message(
        interaction,
        context,
        text=texts.HELP_TEXT,
        reply_markup=Keyboards.help_screen(),
    )


@clear_messages
async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interaction = await get_interaction(update)
    await send_tracked_message(
        interaction,
        context,
        text=texts.INFO_TEXT,
        reply_markup=Keyboards.info_screen(),
    )


@clear_messages
@load_data_for_register_user
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interaction = await get_interaction(update)
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_tracked_message(
            interaction,
            context,
            text=texts.ADMIN_ONLY,
            reply_markup=Keyboards.help_screen(),
        )
        return
    await send_tracked_message(
        interaction,
        context,
        text=texts.ADMIN_PANEL,
        reply_markup=Keyboards.admin_panel(),
    )


@catch_error(START_NOTIFICATIONS_ERROR)
@clear_messages
@load_data_for_register_user
async def start_notifications(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    if not await check_authorization(query, context):
        return
    context.job_queue.run_repeating(
        periodic_check,
        interval=PERIODIC_CHECK_INTERVAL,
        first=PERIODIC_CHECK_FIRST,
        name=f'price_check_{query.message.chat.id}',
        data=dict(
            jwt_token=context.user_data['account']['jwt_token'],
            chat_id=query.from_user.id,
        ),
    )
    await send_tracked_message(
        query,
        context,
        text=texts.NOTIFICATIONS_ON,
        reply_markup=Keyboards.notifications_on(),
    )


def handlers_installer(application: ApplicationBuilder) -> None:
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', menu))
    application.add_handler(CommandHandler('help', help_screen))
    application.add_handler(CommandHandler('info', info))
    application.add_handler(
        CallbackQueryHandler(menu, pattern=f'^{MENU}$')
    )
    application.add_handler(
        CallbackQueryHandler(help_screen, pattern=f'^{HELP}$')
    )
    application.add_handler(
        CallbackQueryHandler(info, pattern=f'^{BOT_INFO}$')
    )
    application.add_handler(
        CallbackQueryHandler(admin_panel, pattern=f'^{ADMIN_PANEL}$')
    )
    application.add_handler(
        CallbackQueryHandler(
            start_notifications, pattern=f'^{START_NOTIFICATIONS}$'
        )
    )
    application.add_handler(
        CallbackQueryHandler(cancel_to_menu, pattern=f'^nav_cancel$')
    )
