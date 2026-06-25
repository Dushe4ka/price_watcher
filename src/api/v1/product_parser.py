"""Unified product parsing for track API."""

from decimal import Decimal

from src.parsers import get_parser
from src.parsers.base import ParsedProduct
from src.schemas.track import TrackDBCreate, TrackUpdate


async def fetch_product_data(
    marketplace: str,
    article: str,
) -> ParsedProduct:
    parser = get_parser(marketplace)
    return await parser.parse_product(article)


def apply_parsed_product_to_track(
    track_schema: TrackDBCreate | TrackUpdate,
    product: ParsedProduct,
) -> TrackDBCreate | TrackUpdate:
    track_schema.current_price = product.price
    track_schema.title = product.title
    if hasattr(track_schema, 'image_url'):
        track_schema.image_url = product.image_url
    return track_schema
