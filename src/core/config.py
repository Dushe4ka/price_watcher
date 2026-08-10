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
    data_collection_warmup_days: int = 1
    admin_telegram_id: str = ''
    market_check_min_price: int = 10000
    market_check_discount_percent: int = 10
    market_check_categories: str = 'electronics,furniture,home'
    crawl_interval_minutes: int = 30
    deals_enabled: bool = True
    max_products_per_category: int = 20
    min_product_rating: float = 4.5
    require_rating: bool = False
    ozon_enabled: bool = True
    ozon_proxy_required: bool = False
    ozon_browser_idle_sec: int = 600
    ozon_request_delay_sec: float = 0.5
    ozon_challenge_timeout_sec: float = 20.0
    ozon_fetch_retries: int = 3
    ozon_max_consecutive_blocks: int = 3
    ozon_block_cooldown_sec: float = 120.0
    ozon_profile_dir: str = '.ozon-profile'
    wb_proxy_required: bool = False
    wb_browser_idle_sec: int = 600
    wb_request_delay_sec: float = 1.0
    wb_challenge_timeout_sec: float = 20.0
    wb_fetch_retries: int = 3
    wb_max_consecutive_blocks: int = 3
    wb_block_cooldown_sec: float = 120.0
    proxy_list: str = ''
    categories_config_path: str = 'config/monitored_categories.yaml'

    @property
    def effective_min_parser_discount(self) -> int:
        if self.min_parser_discount_percent is not None:
            return self.min_parser_discount_percent
        return self.min_discount_percent

    @property
    def market_check_category_slugs(self) -> set[str]:
        if not self.market_check_categories.strip():
            return set()
        return {
            slug.strip()
            for slug in self.market_check_categories.split(',')
            if slug.strip()
        }

    @property
    def admin_telegram_id_list(self) -> list[int]:
        if not self.admin_telegram_id.strip():
            return []
        result: list[int] = []
        for part in self.admin_telegram_id.split(','):
            token = part.strip()
            if token.isdigit():
                result.append(int(token))
        return result

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