from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from src.marketplaces.contracts import (
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.sources.protocols import MarketplaceSourceError
from src.marketplaces.sources.public import (
    OzonPublicSource,
    WildberriesPublicSource,
    YandexPublicSource,
)
from src.crawlers.yandex_market import YandexMarketCategoryCrawler
from src.ozon.client import OzonClient
from src.ozon.constants import OZON_PAGE_JSON_URLS
from src.parsers.utils import BlockedError, retry_request
from src.parsers.yandex_market import YandexMarketParser
from src.wb.client import WBClient


_FIXTURES = Path(__file__).parent / 'fixtures' / 'marketplaces'


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding='utf-8')


def _client_factory(
    status_code: int,
    body: str,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=body, request=request)

    def factory(**kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    return factory


class DisabledPublicSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unproven_ozon_public_source_is_disabled(self) -> None:
        result = await OzonPublicSource().search_products(
            SearchRequest(query='synthetic', limit=2),
        )
        self.assertEqual(SourceOutcome.DISABLED, result.outcome)
        self.assertIsNone(result.value)

    async def test_unproven_wb_public_source_is_disabled(self) -> None:
        result = await WildberriesPublicSource().search_products(
            SearchRequest(query='synthetic', limit=2),
        )
        self.assertEqual(SourceOutcome.DISABLED, result.outcome)
        self.assertIsNone(result.value)


class YandexPublicSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_maps_valid_html_with_canonical_mapper(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(
                200,
                _fixture('yandex_market/success.html'),
            ),
        )

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        external_ids = tuple(product.external_id for product in result.value)
        self.assertEqual(('930001',), external_ids)
        self.assertEqual(1, result.attempt.item_count)

    async def test_structural_empty_is_empty(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(
                200,
                _fixture('yandex_market/empty.html'),
            ),
        )
        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )
        self.assertEqual(SourceOutcome.EMPTY, result.outcome)

    async def test_challenge_is_not_empty(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(
                200,
                _fixture('yandex_market/challenge.html'),
            ),
        )
        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )
        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(
            SafeErrorCode.CHALLENGE_DETECTED,
            result.attempt.error_code,
        )

    async def test_schema_drift_is_not_empty(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(
                200,
                _fixture('yandex_market/drift.html'),
            ),
        )
        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )
        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_rate_limit_has_explicit_outcome(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(429, 'synthetic rate limit'),
        )
        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )
        self.assertEqual(SourceOutcome.RATE_LIMITED, result.outcome)
        self.assertEqual(
            SafeErrorCode.RATE_LIMITED,
            result.attempt.error_code,
        )

    async def test_product_structural_empty_is_not_found(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(
                200,
                _fixture('yandex_market/empty.html'),
            ),
        )
        result = await source.parse_product(ProductRequest('930404'))
        self.assertEqual(SourceOutcome.NOT_FOUND, result.outcome)

    async def test_product_http_not_found_is_not_found(self) -> None:
        source = YandexPublicSource(
            client_factory=_client_factory(404, 'synthetic missing'),
        )
        result = await source.parse_product(ProductRequest('930404'))
        self.assertEqual(SourceOutcome.NOT_FOUND, result.outcome)


class MarketplaceSourceErrorTests(unittest.TestCase):
    def test_raw_cause_is_not_rendered(self) -> None:
        marker = 'synthetic-sensitive-marker'
        error = MarketplaceSourceError(
            SourceOutcome.TRANSPORT_ERROR,
            SafeErrorCode.TRANSPORT_FAILED,
            cause=RuntimeError(marker),
        )
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error))

    def test_yandex_invalid_url_is_not_rendered(self) -> None:
        marker = 'synthetic-sensitive-url-marker'
        parser = YandexMarketParser()
        with self.assertRaises(ValueError) as raised:
            parser.extract_product_id(f'https://example.test/{marker}')
        self.assertNotIn(marker, str(raised.exception))


class RetryLogRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_decorated_failure_never_logs_exception_marker(self) -> None:
        marker = 'https://example.test/synthetic-sensitive-product-940404'
        calls = 0

        @retry_request
        async def fail_with_url() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError(marker)

        with (
            patch('src.parsers.utils.asyncio.sleep', new=AsyncMock()),
            self.assertLogs('src.parsers.utils', level='WARNING') as captured,
            self.assertRaises(RuntimeError),
        ):
            await fail_with_url()

        self.assertEqual(4, calls)
        self.assertNotIn(marker, '\n'.join(captured.output))


class _OzonResponse:
    status = 403

    async def text(self) -> str:
        return '<html>synthetic captcha</html>'


class _OzonRequest:
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, url: str, headers: object) -> _OzonResponse:
        self.calls += 1
        return _OzonResponse()


class _OzonSession:
    def __init__(self) -> None:
        self.request = _OzonRequest()
        self.page = SimpleNamespace(
            context=SimpleNamespace(request=self.request),
        )

    async def ensure_page(self) -> object:
        return self.page

    def note_block(self) -> None:
        pass

    def note_success(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def rotate_and_restart(self) -> None:
        pass


class _WBSession:
    def __init__(self) -> None:
        self.navigation_calls = 0

    async def ensure_page(self) -> object:
        return object()

    async def goto_and_wait(self, page: object, url: str) -> None:
        self.navigation_calls += 1
        raise BlockedError('synthetic-sensitive-block-detail')

    def note_block(self) -> None:
        pass

    def note_success(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def rotate_and_restart(self) -> None:
        pass


class _WBEvaluationPage:
    async def evaluate(self, extract_js: str) -> list[dict[str, str]]:
        return [{'syntheticUnexpectedField': 'synthetic'}]


class _WBEvaluationSession:
    async def ensure_page(self) -> _WBEvaluationPage:
        return _WBEvaluationPage()

    async def goto_and_wait(
        self,
        page: _WBEvaluationPage,
        url: str,
    ) -> None:
        pass

    def note_success(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _WBEmptyEvaluationPage:
    def __init__(self, html: str) -> None:
        self.html = html
        self.evaluate_calls = 0
        self.content_calls = 0

    async def evaluate(self, extract_js: str) -> list[object]:
        self.evaluate_calls += 1
        return []

    async def content(self) -> str:
        self.content_calls += 1
        return self.html


class _WBEmptyEvaluationSession:
    def __init__(self, html: str) -> None:
        self.page = _WBEmptyEvaluationPage(html)
        self.navigation_calls = 0

    async def ensure_page(self) -> _WBEmptyEvaluationPage:
        return self.page

    async def goto_and_wait(
        self,
        page: _WBEmptyEvaluationPage,
        url: str,
    ) -> None:
        self.navigation_calls += 1

    def note_block(self) -> None:
        pass

    def note_success(self) -> None:
        pass

    async def close(self) -> None:
        pass


class NativeClientFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_ozon_challenge_is_typed_without_retry(self) -> None:
        session = _OzonSession()
        client = OzonClient(session=session)

        with (
            patch('src.ozon.client.settings.ozon_fetch_retries', 2),
            patch('src.ozon.client.asyncio.sleep', return_value=None),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await client.fetch_payload('/synthetic/')

        self.assertEqual(SourceOutcome.CHALLENGE, raised.exception.outcome)
        self.assertEqual(
            len(OZON_PAGE_JSON_URLS),
            session.request.calls,
        )

    async def test_wb_challenge_raises_typed_error_without_retry(self) -> None:
        session = _WBSession()
        client = WBClient(session=session)

        with (
            patch('src.wb.client.settings.wb_fetch_retries', 2),
            patch('src.wb.client.asyncio.sleep', return_value=None),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await client.category_products('https://example.test', 2)

        self.assertEqual(SourceOutcome.CHALLENGE, raised.exception.outcome)
        self.assertEqual(1, session.navigation_calls)

    async def test_wb_unmappable_nonempty_cards_are_drift(self) -> None:
        client = WBClient(session=_WBEvaluationSession())

        with (
            patch('src.wb.client.settings.wb_request_delay_sec', 0),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await client.category_products('https://example.test', 2)

        self.assertEqual(SourceOutcome.PARSE_DRIFT, raised.exception.outcome)

    async def test_wb_empty_js_challenge_is_typed_on_same_page(self) -> None:
        session = _WBEmptyEvaluationSession(
            _fixture('wildberries/challenge.html'),
        )
        client = WBClient(session=session)

        with (
            patch('src.wb.client.settings.wb_request_delay_sec', 0),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await client.category_products('https://example.test', 2)

        self.assertEqual(SourceOutcome.CHALLENGE, raised.exception.outcome)
        self.assertEqual(1, session.navigation_calls)
        self.assertEqual(1, session.page.evaluate_calls)
        self.assertEqual(1, session.page.content_calls)

    async def test_wb_empty_js_drift_is_typed_on_same_page(self) -> None:
        session = _WBEmptyEvaluationSession(
            _fixture('wildberries/drift.html'),
        )
        client = WBClient(session=session)

        with (
            patch('src.wb.client.settings.wb_request_delay_sec', 0),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await client.category_products('https://example.test', 2)

        self.assertEqual(SourceOutcome.PARSE_DRIFT, raised.exception.outcome)
        self.assertEqual(1, session.navigation_calls)
        self.assertEqual(1, session.page.evaluate_calls)
        self.assertEqual(1, session.page.content_calls)

    async def test_wb_empty_js_requires_structural_empty_marker(self) -> None:
        session = _WBEmptyEvaluationSession(
            _fixture('wildberries/empty.html'),
        )
        client = WBClient(session=session)

        with patch('src.wb.client.settings.wb_request_delay_sec', 0):
            product_ids, pre_parsed = await client.category_products(
                'https://example.test',
                2,
            )

        self.assertEqual([], product_ids)
        self.assertEqual({}, pre_parsed)
        self.assertEqual(1, session.page.content_calls)


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request('GET', 'https://example.test')
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                'synthetic status failure',
                request=request,
                response=response,
            )


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls = 0

    async def __aenter__(self) -> _FakeHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def get(self, url: str) -> _FakeResponse:
        self.calls += 1
        return self.response


class YandexCompatibilityOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_parser_schema_drift_raises_typed_error_once(self) -> None:
        client = _FakeHttpClient(
            _FakeResponse(
                200,
                _fixture('yandex_market/drift.html'),
            ),
        )
        parser = YandexMarketParser()

        with (
            patch(
                'src.parsers.yandex_market.create_http_client',
                return_value=client,
            ),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await parser.parse_product('930001')

        self.assertEqual(SourceOutcome.PARSE_DRIFT, raised.exception.outcome)
        self.assertEqual(1, client.calls)

    async def test_crawler_schema_drift_raises_typed_error(self) -> None:
        client = _FakeHttpClient(
            _FakeResponse(
                200,
                _fixture('yandex_market/drift.html'),
            ),
        )
        crawler = YandexMarketCategoryCrawler()

        with (
            patch(
                'src.crawlers.yandex_market.create_http_client',
                return_value=client,
            ),
            self.assertRaises(MarketplaceSourceError) as raised,
        ):
            await crawler.crawl_category(
                'https://example.test/catalog',
                'synthetic',
                limit=2,
            )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, raised.exception.outcome)

    async def test_crawler_preserves_structural_empty(self) -> None:
        client = _FakeHttpClient(
            _FakeResponse(
                200,
                _fixture('yandex_market/empty.html'),
            ),
        )
        crawler = YandexMarketCategoryCrawler()

        with patch(
            'src.crawlers.yandex_market.create_http_client',
            return_value=client,
        ):
            result = await crawler.crawl_category(
                'https://example.test/catalog',
                'synthetic',
                limit=2,
            )

        self.assertEqual([], result.product_ids)


if __name__ == '__main__':
    unittest.main()
