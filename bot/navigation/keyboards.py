from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.handlers.callback_data import (
    ACCOUNT_DATA_CB,
    ACCOUNT_SETTINGS,
    ADD_TRACK,
    ADMIN_FORCE_CRAWL,
    ADMIN_PANEL,
    ADMIN_DEALS_STATUS,
    BOT_INFO,
    DELETE_ACCOUNT_CB,
    EDIT_ACCOUNT_CB,
    HELP,
    LOAD_DATA_CB,
    MENU,
    SHOW_ALL_TRACK,
    START_AUTHORIZATION,
    START_NOTIFICATIONS,
    START_REGISTRATION,
)
from bot.navigation.state import get_user_state, is_admin, is_authorized


class Keyboards:
    """Сборщики inline-клавиатур с единой навигацией «Назад / Меню»."""

    @staticmethod
    def row_back_menu(back_callback: str, back_label: str = '◀️ Назад') -> list:
        return [
            InlineKeyboardButton(back_label, callback_data=back_callback),
            InlineKeyboardButton('🏠 Меню', callback_data=MENU),
        ]

    @staticmethod
    def row_menu_only() -> list:
        return [InlineKeyboardButton('🏠 Главное меню', callback_data=MENU)]

    @classmethod
    def guest_start(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('✨ Создать аккаунт', callback_data=START_REGISTRATION)],
            [InlineKeyboardButton('📖 Как пользоваться', callback_data=HELP)],
        ])

    @classmethod
    def registered_start(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('🔐 Войти', callback_data=START_AUTHORIZATION)],
            [InlineKeyboardButton('🏠 Главное меню', callback_data=MENU)],
            [InlineKeyboardButton('📖 Справка', callback_data=HELP)],
        ])

    @classmethod
    def authorized_start(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('🏠 Главное меню', callback_data=MENU)],
            [InlineKeyboardButton('📖 Справка', callback_data=HELP)],
        ])

    @classmethod
    def main_menu(
        cls,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int | None,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        state = get_user_state(context)

        if state == 'authorized':
            rows.extend([
                [InlineKeyboardButton('📦 Мои товары', callback_data=SHOW_ALL_TRACK)],
                [
                    InlineKeyboardButton('➕ Добавить товар', callback_data=ADD_TRACK),
                    InlineKeyboardButton('🔔 Уведомления', callback_data=START_NOTIFICATIONS),
                ],
            ])
        elif state == 'registered':
            rows.append([
                InlineKeyboardButton('🔐 Войти в аккаунт', callback_data=START_AUTHORIZATION),
            ])
        else:
            rows.append([
                InlineKeyboardButton('✨ Создать аккаунт', callback_data=START_REGISTRATION),
            ])

        rows.append([
            InlineKeyboardButton('👤 Аккаунт', callback_data=ACCOUNT_SETTINGS),
            InlineKeyboardButton('📖 Справка', callback_data=HELP),
        ])
        rows.append([
            InlineKeyboardButton('ℹ️ О боте', callback_data=BOT_INFO),
        ])

        if is_admin(user_id):
            rows.append([
                InlineKeyboardButton('🛠 Админ-панель', callback_data=ADMIN_PANEL),
            ])

        return InlineKeyboardMarkup(rows)

    @classmethod
    def help_screen(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([cls.row_menu_only()])

    @classmethod
    def info_screen(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('📖 Как пользоваться', callback_data=HELP)],
            cls.row_menu_only(),
        ])

    @staticmethod
    def row_cancel() -> list:
        return [InlineKeyboardButton('✖️ Отмена', callback_data=MENU)]

    @classmethod
    def auth_prompt(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([cls.row_menu_only()])

    @classmethod
    def auth_required(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('🔐 Войти', callback_data=START_AUTHORIZATION)],
            cls.row_menu_only(),
        ])

    @classmethod
    def account_menu(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('👤 Мой профиль', callback_data=ACCOUNT_DATA_CB)],
            [InlineKeyboardButton('✏️ Редактировать', callback_data=EDIT_ACCOUNT_CB)],
            [
                InlineKeyboardButton('🔄 Обновить', callback_data=LOAD_DATA_CB),
            ],
            [InlineKeyboardButton('🗑 Удалить аккаунт', callback_data=DELETE_ACCOUNT_CB)],
            cls.row_menu_only(),
        ])

    @classmethod
    def account_profile(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            cls.row_back_menu(ACCOUNT_SETTINGS),
        ])

    @classmethod
    def after_auth(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('🏠 Главное меню', callback_data=MENU)],
            [InlineKeyboardButton('📦 Мои товары', callback_data=SHOW_ALL_TRACK)],
        ])

    @classmethod
    def after_registration(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('🔐 Войти', callback_data=START_AUTHORIZATION)],
            cls.row_menu_only(),
        ])

    @classmethod
    def tracks_list_footer(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Добавить товар', callback_data=ADD_TRACK)],
            cls.row_menu_only(),
        ])

    @classmethod
    def tracks_empty(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('➕ Добавить первый товар', callback_data=ADD_TRACK)],
            cls.row_menu_only(),
        ])

    @classmethod
    def add_track_article(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([cls.row_cancel()])

    @classmethod
    def add_track_price(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([cls.row_cancel()])

    @classmethod
    def select_marketplace(cls) -> InlineKeyboardMarkup:
        from bot.handlers.callback_data import OZON, WILDBERRIES

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton('Wildberries', callback_data=f'track_{WILDBERRIES}'),
                InlineKeyboardButton('Ozon (скоро)', callback_data=f'track_{OZON}'),
            ],
            cls.row_cancel(),
        ])

    @classmethod
    def track_created(cls, track_id: int) -> InlineKeyboardMarkup:
        from bot.navigation.helpers import get_track_keyboard

        return InlineKeyboardMarkup(get_track_keyboard(track_id))

    @classmethod
    def track_nav(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            cls.row_back_menu(SHOW_ALL_TRACK, '◀️ К списку'),
        ])

    @classmethod
    def confirm_delete(cls) -> InlineKeyboardMarkup:
        from bot.handlers.callback_data import CANCEL_DELETE, CONFIRM_DELETE

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton('✅ Да, удалить', callback_data=CONFIRM_DELETE),
                InlineKeyboardButton('◀️ Назад', callback_data=CANCEL_DELETE),
            ],
        ])

    @classmethod
    def after_delete(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('📦 К списку товаров', callback_data=SHOW_ALL_TRACK)],
            cls.row_menu_only(),
        ])

    @classmethod
    def notifications_on(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([cls.row_menu_only()])

    @classmethod
    def edit_profile(cls) -> InlineKeyboardMarkup:
        from bot.handlers.callback_data import (
            EDIT_ADD_AVATAR,
            EDIT_EMAIL_CALLBACK,
            EDIT_FULL_NAME_CALLBACK,
            EDIT_PASSWORD,
            FINISH_EDIT,
        )

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton('📸 Фото', callback_data=EDIT_ADD_AVATAR),
            ],
            [
                InlineKeyboardButton('Имя', callback_data=EDIT_FULL_NAME_CALLBACK),
                InlineKeyboardButton('Email', callback_data=EDIT_EMAIL_CALLBACK),
                InlineKeyboardButton('Пароль', callback_data=EDIT_PASSWORD),
            ],
            [
                InlineKeyboardButton('💾 Сохранить', callback_data=FINISH_EDIT),
            ],
            cls.row_back_menu(ACCOUNT_SETTINGS),
        ])

    @classmethod
    def after_edit(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            cls.row_back_menu(ACCOUNT_SETTINGS),
        ])

    @classmethod
    def admin_panel(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('📊 Статус канала', callback_data=ADMIN_DEALS_STATUS)],
            [InlineKeyboardButton('🔄 Запустить обход', callback_data=ADMIN_FORCE_CRAWL)],
            cls.row_menu_only(),
        ])

    @classmethod
    def admin_back(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            cls.row_back_menu(ADMIN_PANEL, '◀️ К админке'),
        ])
