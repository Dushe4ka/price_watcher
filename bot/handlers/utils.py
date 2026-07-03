import os
import re

from aiohttp import ClientSession
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet
from telegram import (CallbackQuery, InlineKeyboardMarkup, InputFile, Update)
from telegram.ext import ContextTypes, ConversationHandler

from bot.endpoints import GET_USER_BY_TELEGRAM_ID
from bot.handlers.constants import PARSE_MODE
from bot.navigation import copy as nav_texts

password_hasher = PasswordHasher()

fernet = Fernet(
    os.getenv(
        'JWT_SECRET_KEY',
        'A8zOVVp4FMb93RD03n0O25FwAYmTxmTQhF3kPBnLJ6E='
    )
)


# Вспомогательные утилиты.
def catch_error(error_message: str, conv=False):
    """Добавляет хандлерам try-except конструкцию."""
    def decorator(handler):
        async def wrapper(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            *args, **kwargs
        ):
            try:
                return await handler(update, context, *args, **kwargs)
            except Exception as error:
                try:
                    interaction = await get_interaction(update)
                    await send_tracked_message(
                        interaction, context, text=error_message,
                    )
                except Exception:
                    if update.effective_chat:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=error_message,
                        )
                print(str(error))
                if conv:
                    return ConversationHandler.END
        return wrapper
    return decorator


def get_telegram_id(interaction: Update | CallbackQuery):
    """Находит телеграм id пользователя."""
    if isinstance(interaction, Update):
        return interaction.message.from_user.id
    return interaction.from_user.id


def add_message_to_delete_list(message, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет сообщение в очередь на удаление."""
    if not context.user_data.get('last_message_ids'):
        context.user_data['last_message_ids'] = list()
    context.user_data['last_message_ids'].append(message.message_id)


async def check_password(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    entered_password: str,
    hashed_user_password: str
) -> bool:
    """Проверяет, совпадает ли введенный пароль с БД."""
    try:
        password_hasher.verify(
            hashed_user_password, entered_password
        )
        return True
    except VerifyMismatchError:
        await update.message.reply_text(
            'Вы ввели неправильный пароль 🚫\n'
            'Попробуйте еще раз.'
        )
        return False


async def check_authorization(
    interaction: Update | CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Проверяет, авторизован ли пользователь."""
    if not context.user_data.get('account', {}).get('jwt_token'):
        from bot.navigation.keyboards import Keyboards

        await send_tracked_message(
            interaction,
            context,
            text=nav_texts.AUTH_REQUIRED,
            reply_markup=Keyboards.auth_required(),
            parse_mode=PARSE_MODE,
        )
        return False
    return True


async def load_user_data(
    session: ClientSession,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """Загрузка пользовательский данных (для тестирования)."""
    async with session.post(
        GET_USER_BY_TELEGRAM_ID, json=dict(
            telegram_id=update.message.from_user.id
        )
    ) as response:
        user_data = await response.json()
        if user_data:
            for field, value in user_data.items():
                context.user_data['account'][field] = value


def escape_markdown_v2(text: str) -> str:
    """
    Экранирует спецсимволы MarkdownV2, чтобы избежать ошибок Telegram.
    """
    return re.sub(r'([\\_*[\]()~`>#+\-=|{}.!])', r'\\\1', text)


async def get_interaction(update: Update) -> Update | CallbackQuery:
    """Возвращает callback query или update с сообщением."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        return query
    if update.message:
        return update
    raise ValueError('Update has no message or callback_query')


def get_chat_id(interaction: Update | CallbackQuery) -> int:
    if isinstance(interaction, CallbackQuery):
        if interaction.message:
            return interaction.message.chat_id
        return interaction.from_user.id
    if interaction.effective_chat:
        return interaction.effective_chat.id
    if interaction.message:
        return interaction.message.chat_id
    raise ValueError('Cannot resolve chat_id from interaction')


async def send_tracked_message(
    interaction: Update | CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = PARSE_MODE,
) -> None:
    """Отправляет сообщение в чат (работает и для callback, и для команд)."""
    message = await context.bot.send_message(
        chat_id=get_chat_id(interaction),
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    add_message_to_delete_list(message, context)


def get_headers(
    context: ContextTypes.DEFAULT_TYPE,
) -> dict[str, str]:
    """Собирает заголовок для прохождения авторизации."""
    return dict(
        Authorization=(
            f'Bearer {context.user_data["account"]["jwt_token"]}'
        ),
    )


def decode_jwt_token(encoded_jwt_token):
    decoded_jwt_token = fernet.decrypt(encoded_jwt_token.encode())
    return decoded_jwt_token.decode()


async def send_tracked_photo(
    interaction: Update | CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    caption: str,
    photo: InputFile,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = PARSE_MODE,
) -> None:
    """Отправляет фото в чат и отслеживает сообщение."""
    message = await context.bot.send_photo(
        chat_id=get_chat_id(interaction),
        photo=photo,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    add_message_to_delete_list(message, context)

