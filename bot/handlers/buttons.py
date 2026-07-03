"""Legacy-обёртки. Новый код использует bot.navigation.Keyboards."""

from bot.navigation.helpers import get_track_keyboard
from bot.navigation.keyboards import Keyboards

# Списки кнопок для InlineKeyboardMarkup(...) — через .inline_keyboard
GO_BACK_NEW_TARGET_PRICE_BUTTONS = Keyboards.track_nav().inline_keyboard
SELECT_MARKETPLACE_BUTTONS = Keyboards.select_marketplace().inline_keyboard
CHECK_HISTORY_BUTTONS = Keyboards.track_nav().inline_keyboard
CONFIRM_TRACK_DELETE_BUTTONS = Keyboards.confirm_delete().inline_keyboard
FINISH_DELETE_TRACK_BUTTONS = Keyboards.after_delete().inline_keyboard
START_NOTIFICATIONS_BUTTONS = Keyboards.notifications_on().inline_keyboard
GET_NEW_TARGET_PRICE_BUTTONS = Keyboards.track_nav().inline_keyboard
ACCOUNT_SETTINGS_BUTTONS = Keyboards.account_menu().inline_keyboard
LOAD_ACCOUNT_DATA = Keyboards.account_profile().inline_keyboard
CHECK_ACCOUNT_DATA_BUTTONS = Keyboards.account_profile().inline_keyboard
FINISH_REGISTRATION_BUTTONS = Keyboards.after_registration().inline_keyboard
FINISH_AUTHORIZATION_BUTTONS = Keyboards.after_auth().inline_keyboard
EDIT_BUTTONS = Keyboards.edit_profile().inline_keyboard
FINISH_EDIT_BUTTONS = Keyboards.after_edit().inline_keyboard


def tracks_list_footer():
    return Keyboards.tracks_list_footer().inline_keyboard


def tracks_empty():
    return Keyboards.tracks_empty().inline_keyboard


def get_create_track_buttons(track_id: int):
    return get_track_keyboard(track_id)
