from bot.handlers.callback_data import CHECK_HISTORY, DELETE_TRACK, MENU, SHOW_ALL_TRACK


def get_track_keyboard(track_id: int) -> list:
    from telegram import InlineKeyboardButton

    return [
        [
            InlineKeyboardButton(
                '✏️ Изменить цель',
                callback_data=f'track_refresh_target_price_{track_id}',
            ),
            InlineKeyboardButton(
                '🗑 Удалить',
                callback_data=f'{DELETE_TRACK}_{track_id}',
            ),
        ],
        [
            InlineKeyboardButton(
                '📈 История цен',
                callback_data=f'{CHECK_HISTORY}_{track_id}',
            ),
        ],
        [
            InlineKeyboardButton('◀️ К списку', callback_data=SHOW_ALL_TRACK),
            InlineKeyboardButton('🏠 Меню', callback_data=MENU),
        ],
    ]
