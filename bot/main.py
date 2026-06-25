import logging
import os

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

from bot.deals_scheduler import bot_post_init
from bot.handlers import (base_installer_handlers, track_handler_installer,
                          user_installer_handlers)
from bot.handlers.deals_admin import deals_admin_handlers_installer
from bot.handlers.deal_moderation import deal_moderation_handlers_installer


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)

load_dotenv()


def main():
    application = (
        ApplicationBuilder()
        .token(os.getenv('TELEGRAM_BOT_TOKEN'))
        .post_init(bot_post_init)
        .build()
    )
    base_installer_handlers(application)
    user_installer_handlers(application)
    track_handler_installer(application)
    deals_admin_handlers_installer(application)
    deal_moderation_handlers_installer(application)
    application.run_polling()


if __name__ == '__main__':
    main()
