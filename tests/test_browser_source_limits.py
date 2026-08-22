from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from src.marketplaces.contracts import ProductRequest, SourceOutcome
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.sources.browser import YandexMarketBrowserSource
from tests.browser_source_fakes import (
    FakeCoordinator,
    FakeManager,
    FakePage,
    HangingContentPage,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'marketplaces'


def _fixture(path: str) -> str:
    return (FIXTURES / path).read_text(encoding='utf-8')


class BrowserSourceContentLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_at_encoded_byte_limit_is_parsed(self) -> None:
        html = _fixture('yandex_market/success.html') + '<!--я-->'
        page = FakePage(html=html)
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=len(html.encode('utf-8')),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)

    async def test_multibyte_content_over_encoded_byte_limit_is_not_parsed(
        self,
    ) -> None:
        html = _fixture('yandex_market/success.html') + '<!--я-->'
        page = FakePage(html=html)
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=len(html.encode('utf-8')) - 1,
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual(
            SafeErrorCode.CONTENT_TOO_LARGE,
            result.attempt.error_code,
        )

    async def test_invalid_utf8_surrogate_is_typed_parse_drift(self) -> None:
        page = FakePage(html='<html>\ud800</html>')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual(SafeErrorCode.PARSE_DRIFT, result.attempt.error_code)


class BrowserSourceDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_exhaustion_is_timeout_and_closes_page(
        self,
    ) -> None:
        page = HangingContentPage(html='unused')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)
        self.assertTrue(page.closed)

    async def test_late_cancelled_capture_cannot_mutate_after_timeout(
        self,
    ) -> None:
        page = HangingContentPage(html='unused')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )

        result = await source.parse_product(ProductRequest('930001'))
        await asyncio.sleep(0.01)

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(0, page.late_mutations)


if __name__ == '__main__':
    unittest.main()
