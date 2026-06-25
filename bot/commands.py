from telegram import BotCommand, BotCommandScopeAllPrivateChats
from telegram.ext import Application

BOT_COMMANDS: list[BotCommand] = [
    BotCommand('start', 'Запустить бота'),
    BotCommand('menu', 'Главное меню'),
    BotCommand('info', 'Информация о боте'),
    BotCommand('auth', 'Авторизация'),
    BotCommand('account_settings', 'Настройки аккаунта'),
    BotCommand('deals_status', 'Статус автопостинга скидок'),
    BotCommand('force_crawl', 'Запустить обход категорий'),
]


async def setup_bot_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeAllPrivateChats(),
    )
