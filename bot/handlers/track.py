from decimal import Decimal
from http import HTTPStatus

import aiohttp
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler)

from bot.endpoints import (CREATE_NEW_TRACK, DELETE_TRACK_BY_ID,
                           GET_TRACKS_PRICE_HISTORY, USERS_TRACKS,
                           USERS_TRACKS_BY_ID)
from bot.handlers.buttons import (CONFIRM_TRACK_DELETE_BUTTONS,
                                  FINISH_DELETE_TRACK_BUTTONS,
                                  GO_BACK_NEW_TARGET_PRICE_BUTTONS,
                                  get_create_track_buttons,
                                  tracks_empty, tracks_list_footer)
from bot.handlers.callback_data import (ADD_TRACK, CANCEL_DELETE,
                                        CHECK_HISTORY, CONFIRM_DELETE,
                                        DELETE_TRACK, MENU, SHOW_ALL_TRACK)
from bot.handlers.constants import MESSAGE_HANDLERS, PARSE_MODE
from bot.handlers.pre_process import (clear_messages,
                                      load_data_for_register_user)
from bot.handlers.utils import (catch_error, check_authorization, get_headers,
                                get_interaction, send_tracked_message)
from bot.handlers.validators import validate_price
from bot.navigation import copy as nav_texts
from bot.navigation.handlers import cancel_to_menu
from bot.navigation.helpers import get_track_keyboard
from bot.navigation.keyboards import Keyboards
from bot.services.track_link_parser import (is_track_url, parse_track_input)

# Состояния для ConversationHandler
SAVE_NEW_TARGET_PRICE = 'save_new_target_price'
TRACK_FLOW_INPUT = 'input'
TRACK_FLOW_PRICE = 'price'
FINISH_DELETE_TRACK = 'delete_track'

# Сообщения для reply_text
SHOW_ALL_ERROR = (
    'Что-то пошло не так при загрузке отслеживаемых товаров! ❌\n'
    'Попробуйте еще раз!'
)
EMPTY_TRACKS = nav_texts.TRACKS_EMPTY
SHOW_ALL_AUTH_ERROR = nav_texts.AUTH_REQUIRED
TRACK_REFRESH_ERROR = (
    'Что-то пошло не так при обновлении отслеживаемого товара! ❌\n'
    'Попробуйте еще раз!'
)
PRICE_HISTORY_ERROR = (
    'Ошибка при загрузке истории товара! ❌'
)
CREATE_BAD_REQUEST_ERROR = """
{error_message}
Попробуйте указать данные для товара заново.
"""
OUTDATED_AUTHORIZATION_ERROR = """
Повторите авторизацию! /auth
Срок действия истек 😢
"""
SHORT_TRACK_CARD = """
<b>🛒 {title}</b>  <code>{article}</code>
_____________________________________
💸 <b>Текущая цена:</b> <code>{current_price}₽</code>
🎯 <b>Желаемая цена:</b> <code>{target_price}₽</code>
🏷️ <b>Статус:</b> {status}
_____________________________________
<b>ID:</b> <code>{id}</code>
"""
PRICE_HISTORY_CARD = """
<b>💰 Цена:</b> {price}₽
<b>📅 Дата:</b> {date} {time}
"""
TRACKS_RESULT_MESSAGE = """
<b>📊 Итого:</b>
_____________________________________
<b>📦 Всего отслеживаемых товаров:</b> <code>{track_count}</code>
<b>📉 Цена ниже желаемой:</b> <code>{true_track_count}</code>
<b>📈 Цена выше желаемой:</b> <code>{false_track_count}</code>
"""


SELECT_TARGET_PRICE_MESSAGE = nav_texts.ADD_TRACK_PRICE
QUICK_ADD_PROMPT = nav_texts.ADD_TRACK_INTRO
PARSE_INPUT_FAILED = nav_texts.TRACK_INPUT_FAILED
SUCCESS_CREATE_TRACK_MESSAGE = nav_texts.ADD_TRACK_SUCCESS
CREATE_NEW_TRACK_ERROR = 'Ошибка при создании нового товара ⚠️'
DELETE_TRACK_ERROR = 'Ошибка при удалении товара из отслеживаемых ⚠️'

TRACK_CARD = """
<b>{title}</b> - <code>{article}</code>
_________________________
💸 Текущая цена: <b>{current_price}</b>
🎯 Желаемая цена: <b>{target_price}</b>
Дата создания: <b>{created_at}</b>
Дата последней проверки: <b>{last_checked_at}</b>
"""

NEW_TARGET_PRICE_MESSAGE = """
{track_card}
_________________________
Укажите новую желаемую цену 🏷️
"""
SUCCESS_SAVE_NEW_TARGET_PRICE_MESSAGE = (
    'Цена успешно обновлена на {new_target_price}! ✅'
)
EMPTY_TRACK_HISTORY_MESSAGE = 'История товара пуста('
TRACK_HISTORY_MESSAGE = """
📊 История товара {track_id}
_________________________
{track_card}
"""
TRACK_HISTORY_NAVIGATION = 'Навигация 📋'
CONFIRM_DELETE_MESSAGE = """
{track_card}
_________________________
Вы точно хотите удалить товар с id = {track_id}
"""
CANCEL_DELETE_MESSAGE = 'Удаление товара с id = {track_id} отменено!'
SUCCESS_DELETE_MESSAGE = 'Товар с id = {track_id} успешно удален! ✅'


@catch_error(SHOW_ALL_ERROR)
@clear_messages
@load_data_for_register_user
async def show_all(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    if not await check_authorization(query, context):
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(
            USERS_TRACKS,
            headers=get_headers(context)
        ) as response:
            if response.status == HTTPStatus.UNAUTHORIZED:
                await send_tracked_message(
                    query,
                    context,
                    text=OUTDATED_AUTHORIZATION_ERROR
                )
                return
            tracks = await response.json()
            if not tracks:
                await send_tracked_message(
                    query,
                    context,
                    text=EMPTY_TRACKS,
                    reply_markup=InlineKeyboardMarkup(tracks_empty()),
                )
                return
            await send_tracked_message(
                query,
                context,
                text=nav_texts.TRACKS_HEADER,
            )
            true_track_count = false_track_count = 0
            for track in tracks:
                if Decimal(track['target_price']) >= Decimal(track['current_price']):
                    true_track_count += 1
                else:
                    false_track_count += 1
                track_card = SHORT_TRACK_CARD.format(
                    title=track.get('title'),
                    id=track.get('id'),
                    article=track.get('article'),
                    current_price=track.get('current_price'),
                    target_price=track.get('target_price'),
                    status='✅' if track['notified'] else '❌'
                )
                await send_tracked_message(
                    query,
                    context,
                    text=track_card,
                    reply_markup=InlineKeyboardMarkup(
                        get_track_keyboard(track["id"])
                    )
                )
            await send_tracked_message(
                query,
                context,
                text=TRACKS_RESULT_MESSAGE.format(
                    track_count=len(tracks),
                    true_track_count=true_track_count,
                    false_track_count=false_track_count
                ),
                reply_markup=InlineKeyboardMarkup(tracks_list_footer()),
            )


async def get_new_target_price(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    context.user_data['track_id'] = query.data.split('_')[-1]
    if not await check_authorization(query, context):
        return ConversationHandler.END
    await query.message.edit_text(
        text=NEW_TARGET_PRICE_MESSAGE.format(
            track_card=query.message.text
        ),
        parse_mode=PARSE_MODE
    )
    return SAVE_NEW_TARGET_PRICE


@catch_error(TRACK_REFRESH_ERROR, conv=True)
@clear_messages
@load_data_for_register_user
async def target_price_refresh(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    new_target_price = update.message.text
    await update.message.delete()
    validated_price = await validate_price(
        update, context, new_target_price
    )
    if not validated_price:
        return SAVE_NEW_TARGET_PRICE
    async with aiohttp.ClientSession() as session:
        refresh_data = dict(
            target_price=validated_price
        )
        async with session.patch(
            USERS_TRACKS_BY_ID.format(id=context.user_data['track_id']),
            headers=get_headers(context),
            json=refresh_data
        ):
            await send_tracked_message(
                update,
                context,
                text=SUCCESS_SAVE_NEW_TARGET_PRICE_MESSAGE.format(
                    new_target_price=new_target_price
                ),
                reply_markup=InlineKeyboardMarkup(
                    GO_BACK_NEW_TARGET_PRICE_BUTTONS
                )
            )
            return ConversationHandler.END


def _clear_track_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop('track_flow', None)
    context.user_data.pop('new_track', None)


def _marketplace_label(marketplace: str) -> str:
    labels = {
        'wildberries': 'Wildberries',
        'ozon': 'Ozon',
        'yandex_market': 'Яндекс.Маркет',
    }
    return labels.get(marketplace, marketplace)


@clear_messages
@load_data_for_register_user
async def start_add_track(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    interaction = await get_interaction(update)
    if not await check_authorization(interaction, context):
        return
    context.user_data['new_track'] = {}
    context.user_data['track_flow'] = TRACK_FLOW_INPUT
    await send_tracked_message(
        interaction,
        context,
        text=QUICK_ADD_PROMPT,
        reply_markup=Keyboards.add_track_article(),
    )


@clear_messages
@load_data_for_register_user
async def handle_track_flow_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.message.text:
        return
    if not context.user_data.get('account', {}).get('jwt_token'):
        return

    text = update.message.text.strip()
    flow = context.user_data.get('track_flow')

    if flow not in (TRACK_FLOW_INPUT, TRACK_FLOW_PRICE):
        if is_track_url(text):
            context.user_data['new_track'] = {}
            context.user_data['track_flow'] = TRACK_FLOW_INPUT
            await _receive_track_input(update, context, text)
        return

    if flow == TRACK_FLOW_INPUT:
        await _receive_track_input(update, context, text)
    elif flow == TRACK_FLOW_PRICE:
        await _receive_target_price(update, context)


async def _receive_track_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    parsed = parse_track_input(text)
    if not parsed:
        await send_tracked_message(
            update,
            context,
            text=PARSE_INPUT_FAILED,
            reply_markup=Keyboards.add_track_article(),
        )
        return

    await update.message.delete()
    context.user_data['new_track'] = {
        'marketplace': parsed.marketplace,
        'article': parsed.article,
    }
    context.user_data['track_flow'] = TRACK_FLOW_PRICE
    await send_tracked_message(
        update,
        context,
        text=(
            f'✅ Распознано: <b>{_marketplace_label(parsed.marketplace)}</b>\n'
            f'Артикул: <code>{parsed.article}</code>\n\n'
            f'{SELECT_TARGET_PRICE_MESSAGE}'
        ),
        reply_markup=Keyboards.add_track_price(),
    )


@catch_error(CREATE_NEW_TRACK_ERROR)
@clear_messages
@load_data_for_register_user
async def _receive_target_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    target_price = await validate_price(
        update,
        context,
        update.message.text,
    )
    if not target_price:
        return

    context.user_data['new_track']['target_price'] = target_price
    await update.message.delete()

    async with aiohttp.ClientSession() as session:
        async with session.post(
            CREATE_NEW_TRACK,
            headers=get_headers(context),
            json=context.user_data['new_track'],
        ) as response:
            if response.status == HTTPStatus.UNAUTHORIZED:
                await send_tracked_message(
                    update,
                    context,
                    text=OUTDATED_AUTHORIZATION_ERROR,
                )
                return
            if response.status == HTTPStatus.BAD_REQUEST:
                error_data = await response.json()
                detail = error_data.get('detail', 'Неизвестная ошибка')
                context.user_data['track_flow'] = TRACK_FLOW_PRICE
                await send_tracked_message(
                    update,
                    context,
                    text=CREATE_BAD_REQUEST_ERROR.format(
                        error_message=detail,
                    ),
                    reply_markup=Keyboards.tracks_list_footer(),
                )
                return

            new_track = await response.json()

    await send_tracked_message(
        update,
        context,
        text=SUCCESS_CREATE_TRACK_MESSAGE,
    )
    track_card = TRACK_CARD.format(
        title=new_track['title'],
        article=new_track['article'],
        current_price=new_track['current_price'],
        target_price=new_track['target_price'],
        created_at=new_track['created_at'],
        last_checked_at=new_track['last_checked_at'],
    )
    await send_tracked_message(
        update,
        context,
        text=track_card,
        reply_markup=InlineKeyboardMarkup(
            get_create_track_buttons(new_track['id']),
        ),
    )
    _clear_track_flow(context)


@catch_error(PRICE_HISTORY_ERROR)
@clear_messages
@load_data_for_register_user
async def check_track_history(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Получает последнии записи в истории товара."""
    query = update.callback_query
    await query.answer()
    track_id = query.data.split('_')[-1]
    async with aiohttp.ClientSession() as session:
        async with session.get(
            GET_TRACKS_PRICE_HISTORY.format(
                track_id=track_id
            ),
            headers=get_headers(context)
        ) as response:
            writes = await response.json()
            if not writes:
                await query.message.reply_text(
                    EMPTY_TRACK_HISTORY_MESSAGE,
                    reply_markup=Keyboards.track_nav(),
                )
                return
            await send_tracked_message(
                query,
                context,
                text=TRACK_HISTORY_MESSAGE.format(
                    track_id=track_id,
                    track_card=query.message.text
                )
            )
            for write in writes:
                date, time = write['created_at'].split('T')
                await send_tracked_message(
                    query,
                    context,
                    text=PRICE_HISTORY_CARD.format(
                        price=write['price'],
                        date=date,
                        time=time
                    )
                )
            await send_tracked_message(
                query,
                context,
                text=TRACK_HISTORY_NAVIGATION,
                reply_markup=Keyboards.track_nav(),
            )


async def confirm_track_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    track_card = query.message.text
    track_id = query.data.split('_')[-1]
    context.user_data['deleted_track'] = dict()
    context.user_data['deleted_track']['id'] = track_id
    await query.message.edit_text(
        text=CONFIRM_DELETE_MESSAGE.format(
            track_card=track_card,
            track_id=track_id
        ),
        reply_markup=InlineKeyboardMarkup(CONFIRM_TRACK_DELETE_BUTTONS),
        parse_mode=PARSE_MODE
    )
    return FINISH_DELETE_TRACK


@catch_error(DELETE_TRACK_ERROR, conv=True)
@clear_messages
@load_data_for_register_user
async def finish_delete_track(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    if query.data == CANCEL_DELETE:
        await send_tracked_message(
            query,
            context,
            text=CANCEL_DELETE_MESSAGE.format(
                track_id=context.user_data['deleted_track']['id']
            ),
            reply_markup=InlineKeyboardMarkup(FINISH_DELETE_TRACK_BUTTONS)
        )
        return ConversationHandler.END
    async with aiohttp.ClientSession() as session:
        async with session.delete(
            DELETE_TRACK_BY_ID.format(
                id=context.user_data['deleted_track']['id']
            ),
            headers=get_headers(context)
        ):
            await send_tracked_message(
                query,
                context,
                text=SUCCESS_DELETE_MESSAGE.format(
                    track_id=context.user_data['deleted_track']['id']
                ),
                reply_markup=InlineKeyboardMarkup(FINISH_DELETE_TRACK_BUTTONS)
            )
    return ConversationHandler.END


def handlers_installer(
    application: ApplicationBuilder
) -> None:
    application.add_handler(
        CallbackQueryHandler(
            show_all, pattern=f'^{SHOW_ALL_TRACK}$'
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            check_track_history, pattern=f'^{CHECK_HISTORY}_'
        )
    )
    refresh_target_price_conversation_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                get_new_target_price, pattern='^track_refresh_target_price_',
            ),
        ],
        states={
            SAVE_NEW_TARGET_PRICE: [
                MessageHandler(MESSAGE_HANDLERS, target_price_refresh),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_menu, pattern=f'^{MENU}$'),
            CommandHandler('menu', cancel_to_menu),
            MessageHandler(MESSAGE_HANDLERS, target_price_refresh),
        ],
        per_message=True,
    )
    delete_track_conversation_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                confirm_track_delete, pattern=f'^{DELETE_TRACK}_'
            )
        ],
        states={
            FINISH_DELETE_TRACK: [
                CallbackQueryHandler(
                    finish_delete_track,
                    pattern=f'^({CONFIRM_DELETE}|{CANCEL_DELETE})$'
                )
            ]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_to_menu, pattern=f'^{MENU}$'),
            CommandHandler('menu', cancel_to_menu),
            CallbackQueryHandler(
                finish_delete_track,
                pattern=f'^({CONFIRM_DELETE}|{CANCEL_DELETE})$',
            ),
        ],
        per_message=True,
    )
    application.add_handler(
        CallbackQueryHandler(start_add_track, pattern=f'^{ADD_TRACK}$'),
    )
    application.add_handler(CommandHandler('add', start_add_track))
    application.add_handler(
        MessageHandler(MESSAGE_HANDLERS, handle_track_flow_message),
        group=1,
    )
    application.add_handler(
        refresh_target_price_conversation_handler,
    )
    application.add_handler(
        delete_track_conversation_handler,
    )
