from __future__ import annotations

import re

from src.parsers.base import BaseParser, ParsedProduct
from src.parsers.utils import NotFoundError, ParsingError, retry_request
from src.wb.client import wb_client
from src.wb.constants import build_product_url

_PRODUCT_ID_RE = re.compile(r'wildberries\.ru/catalog/(\d+)')


class WildberriesParser(BaseParser):
    marketplace = 'wildberries'

    def extract_product_id(self, url: str) -> str:
        match = _PRODUCT_ID_RE.search(url)
        if not match:
            raise ValueError(
                f'Cannot extract Wildberries product ID from URL: {url}'
            )
        return match.group(1)

    def build_url(self, product_id: str) -> str:
        return build_product_url(product_id)

    @retry_request
    async def parse_product(self, url_or_id: str) -> ParsedProduct:
        if url_or_id.startswith('http') or 'wildberries.ru' in url_or_id:
            product_id = self.extract_product_id(url_or_id)
        else:
            product_id = url_or_id.strip()

        product = await wb_client.product_detail(product_id)
        if product is None:
            raise NotFoundError(f'Wildberries product {product_id} not found')
        if product.price <= 0:
            raise ParsingError(f'No price for Wildberries product {product_id}')
        return product
