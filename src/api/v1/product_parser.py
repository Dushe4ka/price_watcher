"""Unified product parsing for track API."""

from src.marketplaces.contracts import MarketplaceResult
from src.parsers.base import (
    ParsedProduct,
    parse_product,
    parse_product_result,
)
from src.schemas.track import TrackDBCreate, TrackUpdate


async def fetch_product_data(
    marketplace: str,
    article: str,
) -> ParsedProduct:
    """Parse one tracked product, preserving the parser error contract."""
    return await parse_product(marketplace, article)


async def fetch_product_result(
    marketplace: str,
    article: str,
) -> MarketplaceResult[ParsedProduct]:
    """Parse one tracked product with full source diagnostics."""
    return await parse_product_result(marketplace, article)


def apply_parsed_product_to_track(
    track_schema: TrackDBCreate | TrackUpdate,
    product: ParsedProduct,
) -> TrackDBCreate | TrackUpdate:
    track_schema.current_price = product.price
    track_schema.title = product.title
    if hasattr(track_schema, 'image_url'):
        track_schema.image_url = product.image_url
    return track_schema
