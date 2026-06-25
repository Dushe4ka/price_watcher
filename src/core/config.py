import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

DEFAULT_APP_TITLE = 'Price Watcher'
DEFAULT_APP_DESCRIPTION = 'Сервис для просмотра цен.'


load_dotenv()

UPLOAD_DIR = 'media'
STATIC_DIR = '/media'


class Settings(BaseSettings):
    db_dialect: str
    db_driver: str
    secret: str
    title: str = DEFAULT_APP_TITLE
    description: str = DEFAULT_APP_DESCRIPTION
    first_superuser_email: str
    first_superuser_password: str
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_port: str
    postgres_host: str
    telegram_bot_token: str = ''
    telegram_channel_id: str = ''
    min_discount_percent: int = 15
    min_parser_discount_percent: int | None = None
    min_database_discount_percent: int = 20
    price_history_retention_days: int = 90
    data_collection_warmup_days: int = 7
    admin_telegram_id: int = 0
    crawl_interval_minutes: int = 30
    deals_enabled: bool = True
    max_products_per_category: int = 20
    proxy_list: str = ''
    categories_config_path: str = 'config/monitored_categories.yaml'

    @property
    def effective_min_parser_discount(self) -> int:
        if self.min_parser_discount_percent is not None:
            return self.min_parser_discount_percent
        return self.min_discount_percent

    @property
    def database_url(self):
        return (
            f'{self.db_dialect}+{self.db_driver}://'
            f'{self.postgres_user}:{self.postgres_password}@'
            f'{self.postgres_host}:{self.postgres_port}'
            f'/{self.postgres_db}'
        )

    @property
    def proxies(self) -> list[str]:
        if not self.proxy_list.strip():
            return []
        return [
            proxy.strip()
            for proxy in self.proxy_list.split(',')
            if proxy.strip()
        ]


settings = Settings()
print(settings.database_url)