from pydantic import BaseModel, Field
from decimal import Decimal


class MarketplaceCategoryConfig(BaseModel):
    marketplace: str
    crawl_url: str


class CategoryConfig(BaseModel):
    slug: str
    hashtag: str
    name: str
    min_discount_percent: int = 15
    marketplaces: list[MarketplaceCategoryConfig] = Field(default_factory=list)


class CategoriesConfig(BaseModel):
    categories: list[CategoryConfig] = Field(default_factory=list)


class PostedDealCreate(BaseModel):
    marketplace: str
    external_id: str
    category_slug: str
    hashtag: str
    title: str
    price: Decimal
    original_price: Decimal | None = None
    discount_percent: int | None = None
    product_url: str | None = None
    image_url: str | None = None
    telegram_message_id: int | None = None


class PostedDealRead(BaseModel):
    id: int
    marketplace: str
    external_id: str
    category_slug: str
    hashtag: str
    title: str
    price: Decimal
    original_price: Decimal | None = None
    discount_percent: int | None = None
    product_url: str | None = None
    image_url: str | None = None
    telegram_message_id: int | None = None

    model_config = {'from_attributes': True}


class DealRunStats(BaseModel):
    crawled: int = 0
    parsed: int = 0
    prices_saved: int = 0
    matched_discount: int = 0
    posted: int = 0
    sent_to_moderation: int = 0
    skipped_duplicate: int = 0
    skipped_threshold: int = 0
    errors: int = 0


class DealModerationCreate(BaseModel):
    tracked_product_id: int | None = None
    marketplace: str
    external_id: str
    category_slug: str
    hashtag: str
    title: str
    price: Decimal
    original_price: Decimal | None = None
    average_price: Decimal | None = None
    parser_discount_percent: int | None = None
    database_discount_percent: int | None = None
    product_url: str | None = None
    image_url: str | None = None
    status: str
    decision_reason: str
    admin_telegram_id: int | None = None
    admin_message_id: int | None = None
    channel_message_id: int | None = None
