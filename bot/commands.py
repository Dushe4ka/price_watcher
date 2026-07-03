from telegram import BotCommand, BotCommandScopeAllPrivateChats
from telegram.ext import Application

BOT_COMMANDS: list[BotCommand] = [
    BotCommand('start', 'Запустить бота'),
    BotCommand('menu', 'Главное меню'),
    BotCommand('help', 'Как пользоваться'),
    BotCommand('add', 'Добавить товар'),
    BotCommand('info', 'О боте'),
    BotCommand('account_settings', 'Аккаунт'),
    BotCommand('deals_status', 'Статус канала (админ)'),
    BotCommand('force_crawl', 'Обход категорий (админ)'),
]


async def setup_bot_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeAllPrivateChats(),
    )
