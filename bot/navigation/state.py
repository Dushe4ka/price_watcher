from telegram import Update
from telegram.ext import ContextTypes

from src.core.config import settings


def is_authorized(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get('account', {}).get('jwt_token'))


def is_registered(context: ContextTypes.DEFAULT_TYPE) -> bool:
    account = context.user_data.get('account', {})
    return bool(account.get('id') or account.get('email'))


def is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.admin_telegram_id_list


def user_display_name(update: Update) -> str:
    user = update.effective_user
    if user.username:
        return f'@{user.username}'
    if user.first_name:
        return user.first_name
    return 'друг'


def get_user_state(context: ContextTypes.DEFAULT_TYPE) -> str:
    if is_authorized(context):
        return 'authorized'
    if is_registered(context):
        return 'registered'
    return 'guest'


def menu_status_line(context: ContextTypes.DEFAULT_TYPE) -> str:
    from bot.navigation import copy as texts

    state = get_user_state(context)
    if state == 'authorized':
        return texts.MENU_STATUS_AUTH
    if state == 'registered':
        return texts.MENU_STATUS_NO_AUTH
    return texts.MENU_STATUS_GUEST


def menu_text(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> str:
    from bot.navigation import copy as texts

    admin_hint = texts.MENU_ADMIN_HINT if is_admin(user_id) else ''
    return texts.MENU_TITLE.format(
        status_line=menu_status_line(context),
        admin_hint=admin_hint,
    )
