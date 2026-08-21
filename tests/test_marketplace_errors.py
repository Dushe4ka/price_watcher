from __future__ import annotations

import unittest

from src.marketplaces.contracts import MarketplaceOperation
from src.marketplaces.errors import MarketplaceOperationError, SafeErrorCode


class MarketplaceOperationErrorTests(unittest.TestCase):
    def test_error_string_does_not_include_raw_exception(self) -> None:
        marker = 'sentinel-secret-value'
        error = MarketplaceOperationError(
            marketplace='ozon',
            operation=MarketplaceOperation.PARSE_PRODUCT,
            error_code=SafeErrorCode.TRANSPORT_FAILED,
            attempts=(),
            cause=RuntimeError(marker),
        )

        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error))

    def test_error_exposes_only_safe_operation_metadata(self) -> None:
        error = MarketplaceOperationError(
            marketplace='wildberries',
            operation=MarketplaceOperation.CRAWL_CATEGORY,
            error_code=SafeErrorCode.CONTENT_TOO_LARGE,
            attempts=(),
        )

        self.assertIn('wildberries', str(error))
        self.assertIn('crawl_category', str(error))
        self.assertIn('content_too_large', str(error))


if __name__ == '__main__':
    unittest.main()
