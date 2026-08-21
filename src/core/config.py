import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.marketplaces.contracts import (
    MarketplaceName,
    MarketplaceOperation,
    SourceName,
)

DEFAULT_APP_TITLE = 'Price Watcher'
DEFAULT_APP_DESCRIPTION = 'Сервис для просмотра цен.'


UPLOAD_DIR = 'media'
STATIC_DIR = '/media'

RuntimeRole = Literal['local', 'api', 'bot']
CaptchaAdapterMode = Literal['disabled', 'ohmycaptcha']
SmartCaptchaMode = Literal['disabled', 'frictionless']

_RUNTIME_ROLES = frozenset(('local', 'api', 'bot'))
_MARKETPLACES = frozenset(('wildberries', 'ozon', 'yandex_market'))
_DEFAULT_SOURCE_CHAINS: dict[MarketplaceName, tuple[SourceName, ...]] = {
    'wildberries': (SourceName.BROWSER, SourceName.APIFY),
    'ozon': (SourceName.BROWSER, SourceName.APIFY),
    'yandex_market': (
        SourceName.PUBLIC,
        SourceName.BROWSER,
        SourceName.APIFY,
    ),
}


if os.environ.get('PYTHON_DOTENV_DISABLED') != '1':
    load_dotenv()


def parse_source_chain(
    value: str,
    default: tuple[SourceName, ...],
) -> tuple[SourceName, ...]:
    """Parse a comma-separated source chain without changing its topology."""
    raw = value.strip()
    if not raw:
        return default
    sources = tuple(SourceName(part.strip()) for part in raw.split(','))
    if len(sources) != len(set(sources)):
        raise ValueError('source chain cannot contain duplicates')
    return sources


class Settings(BaseSettings):
    """Settings read only from the explicit process environment."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra='forbid',
        validate_default=True,
    )

    db_dialect: str
    db_driver: str
    secret: str = Field(repr=False)
    title: str = DEFAULT_APP_TITLE
    description: str = DEFAULT_APP_DESCRIPTION
    first_superuser_email: str
    first_superuser_password: str = Field(repr=False)
    postgres_user: str
    postgres_password: str = Field(repr=False)
    postgres_db: str
    postgres_port: str
    postgres_host: str
    telegram_bot_token: str = Field(default='', repr=False)
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
    proxy_list: str = Field(default='', repr=False)
    categories_config_path: str = 'config/monitored_categories.yaml'
    marketplace_runtime_role: RuntimeRole = 'local'
    browser_profile_root: str = 'browser-profiles'
    wildberries_source_chain: str = 'browser,apify'
    ozon_source_chain: str = 'browser,apify'
    yandex_market_source_chain: str = 'public,browser,apify'
    apify_token: SecretStr = Field(
        default_factory=lambda: SecretStr(''),
        validation_alias=AliasChoices(
            'apify_token',
            'apify_api_token',
            'APIFY_TOKEN',
            'APIFY_API_TOKEN',
        ),
    )
    apify_wildberries_crawl_category_actor_id: str = ''
    apify_wildberries_parse_product_actor_id: str = ''
    apify_wildberries_search_products_actor_id: str = ''
    apify_ozon_crawl_category_actor_id: str = ''
    apify_ozon_parse_product_actor_id: str = ''
    apify_ozon_search_products_actor_id: str = ''
    apify_yandex_market_crawl_category_actor_id: str = ''
    apify_yandex_market_parse_product_actor_id: str = ''
    apify_yandex_market_search_products_actor_id: str = ''
    captcha_adapter_mode: CaptchaAdapterMode = 'disabled'
    ohmycaptcha_api_key: SecretStr = SecretStr('')
    smartcaptcha_mode: SmartCaptchaMode = 'disabled'
    smartcaptcha_client_key: SecretStr = SecretStr('')
    marketplace_total_timeout_sec: int = Field(default=30, gt=0, le=300)
    marketplace_max_content_bytes: int = Field(
        default=2_000_000,
        gt=0,
        le=10_485_760,
    )
    marketplace_retry_max_attempts: int = Field(default=2, ge=1, le=2)
    marketplace_retry_base_delay_ms: int = Field(default=250, ge=0)
    marketplace_retry_max_delay_ms: int = Field(default=1000, ge=0)

    @field_validator(
        'wildberries_source_chain',
        'ozon_source_chain',
        'yandex_market_source_chain',
    )
    @classmethod
    def validate_source_chain(cls, value: str) -> str:
        """Fail fast for source names and duplicate source entries."""
        parse_source_chain(value, ())
        return value

    @field_validator('marketplace_retry_max_delay_ms')
    @classmethod
    def validate_retry_delay_bounds(
        cls,
        value: int,
        info: ValidationInfo,
    ) -> int:
        """Keep retry delays bounded in a configuration independent way."""
        data = info.data
        base_delay = data.get('marketplace_retry_base_delay_ms', 0)
        if value < base_delay:
            raise ValueError(
                'marketplace retry max delay must not be less than base delay'
            )
        return value

    @property
    def runtime_role(self) -> RuntimeRole:
        """Expose the concise role name used by marketplace components."""
        return self.marketplace_runtime_role

    @property
    def apify_api_token(self) -> SecretStr:
        """Preserve the descriptive token accessor for source adapters."""
        return self.apify_token

    def source_chain(
        self,
        marketplace: MarketplaceName,
    ) -> tuple[SourceName, ...]:
        """Return the configured source order for one supported marketplace."""
        if marketplace not in _MARKETPLACES:
            raise ValueError(f'unsupported marketplace: {marketplace}')
        source_value = getattr(self, f'{marketplace}_source_chain')
        default = _DEFAULT_SOURCE_CHAINS[marketplace]
        return parse_source_chain(source_value, default)

    def apify_actor_id(
        self,
        marketplace: MarketplaceName,
        operation: MarketplaceOperation,
    ) -> str:
        """Return only the configured actor for one marketplace operation."""
        if marketplace not in _MARKETPLACES:
            raise ValueError(f'unsupported marketplace: {marketplace}')
        field_name = f'apify_{marketplace}_{operation.value}_actor_id'
        return getattr(self, field_name)

    def profile_dir(
        self,
        role: RuntimeRole,
        marketplace: MarketplaceName,
    ) -> Path:
        """Return a resolved browser profile path contained in its root."""
        if role not in _RUNTIME_ROLES:
            raise ValueError(f'unsupported marketplace runtime role: {role}')
        if marketplace not in _MARKETPLACES:
            raise ValueError(f'unsupported marketplace: {marketplace}')
        root = Path(self.browser_profile_root).expanduser().resolve()
        profile = (root / role / marketplace).resolve()
        try:
            profile.relative_to(root)
        except ValueError as exc:
            raise ValueError('browser profile path escapes its root') from exc
        return profile

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
