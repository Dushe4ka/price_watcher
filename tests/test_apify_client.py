from __future__ import annotations

import json
import unittest
from collections.abc import AsyncIterator

import httpx

from src.core.config import Settings
from src.marketplaces.contracts import (
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.errors import MarketplaceSourceError, SafeErrorCode


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        'db_dialect': 'postgresql',
        'db_driver': 'asyncpg',
        'secret': 'application-secret',
        'first_superuser_email': 'admin@example.invalid',
        'first_superuser_password': 'superuser-password',
        'postgres_user': 'postgres-user',
        'postgres_password': 'postgres-password',
        'postgres_db': 'price-watcher',
        'postgres_port': '5432',
        'postgres_host': 'localhost',
        'apify_token': 'apify-token-sentinel',
        'apify_ozon_search_products_actor_id': 'synthetic-search-actor',
        'apify_ozon_parse_product_actor_id': 'synthetic-product-actor',
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def client_factory(handler: httpx.AsyncBaseTransport) -> object:
    def factory(**kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=handler, **kwargs)

    return factory


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class ApifyActorInputTests(unittest.TestCase):
    def test_search_actor_input_is_code_owned(self) -> None:
        from src.marketplaces.apify_client import build_actor_input

        payload = build_actor_input(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            SearchRequest(query='synthetic query', limit=3),
        )

        self.assertEqual(3, payload['maxItems'])
        self.assertEqual('synthetic query', payload['searchQuery'])
        self.assertNotIn('actorId', payload)
        self.assertNotIn('proxy', payload)

    def test_search_actor_input_repr_redacts_query(self) -> None:
        from src.marketplaces.apify_client import build_actor_input

        query = 'synthetic-query-sentinel'
        payload = build_actor_input(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            SearchRequest(query=query, limit=3),
        )

        self.assertEqual(query, json.loads(json.dumps(payload))['searchQuery'])
        self.assertNotIn(query, repr(payload))
        self.assertNotIn(query, str(payload))

    def test_product_actor_input_builds_fixed_marketplace_url(self) -> None:
        from src.marketplaces.apify_client import build_actor_input

        payload = build_actor_input(
            'ozon',
            MarketplaceOperation.PARSE_PRODUCT,
            ProductRequest('940001'),
        )

        self.assertEqual('https://www.ozon.ru/product/940001/',
                         payload['productUrl'])
        self.assertNotIn('actorId', payload)
        self.assertNotIn('proxy', payload)

    def test_product_actor_input_repr_redacts_product_url(self) -> None:
        from src.marketplaces.apify_client import build_actor_input

        product_id = '940001'
        payload = build_actor_input(
            'ozon',
            MarketplaceOperation.PARSE_PRODUCT,
            ProductRequest(product_id),
        )

        product_url = 'https://www.ozon.ru/product/940001/'
        self.assertEqual(product_url,
                         json.loads(json.dumps(payload))['productUrl'])
        self.assertNotIn(product_id, repr(payload))
        self.assertNotIn(product_url, repr(payload))


class ApifyClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_actor_uses_fixed_route_and_authorization_header(
        self,
    ) -> None:
        from src.marketplaces.apify_client import ApifyClient

        observed: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            observed['url'] = str(request.url)
            observed['authorization'] = request.headers.get('Authorization')
            observed['payload'] = json.loads(request.content)
            return httpx.Response(
                200,
                json=[{'id': '940001'}],
                request=request,
            )

        client = ApifyClient(
            make_settings(),
            client_factory(httpx.MockTransport(handler)),
        )
        result = await client.run_actor(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            SearchRequest(query='synthetic query', limit=3),
        )

        self.assertEqual([{'id': '940001'}], result)
        self.assertEqual(
            'https://api.apify.com/v2/acts/synthetic-search-actor/'
            'run-sync-get-dataset-items',
            observed['url'],
        )
        self.assertEqual('Bearer apify-token-sentinel',
                         observed['authorization'])
        self.assertEqual(
            {'searchQuery': 'synthetic query', 'page': 1, 'maxItems': 3},
            observed['payload'],
        )
        self.assertNotIn('apify-token-sentinel', str(observed['url']))

    async def test_http_statuses_have_safe_typed_outcomes(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        expected = (
            (401, SourceOutcome.AUTH_ERROR, SafeErrorCode.AUTH_FAILED),
            (403, SourceOutcome.AUTH_ERROR, SafeErrorCode.AUTH_FAILED),
            (429, SourceOutcome.RATE_LIMITED, SafeErrorCode.RATE_LIMITED),
            (400, SourceOutcome.INVALID_CONFIG, SafeErrorCode.INVALID_CONFIG),
            (500, SourceOutcome.TRANSPORT_ERROR,
             SafeErrorCode.TRANSPORT_FAILED),
        )
        for status, outcome, error_code in expected:
            with self.subTest(status=status):
                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        status,
                        headers={'Retry-After': '10000'},
                        request=request,
                    )

                client = ApifyClient(
                    make_settings(),
                    client_factory(httpx.MockTransport(handler)),
                )
                with self.assertRaises(MarketplaceSourceError) as raised:
                    await client.run_actor(
                        'ozon',
                        MarketplaceOperation.SEARCH_PRODUCTS,
                        SearchRequest(query='synthetic query', limit=3),
                    )

                self.assertEqual(outcome, raised.exception.outcome)
                self.assertEqual(error_code, raised.exception.error_code)
                self.assertNotIn('apify-token-sentinel', str(raised.exception))
                if status == 429:
                    self.assertLessEqual(
                        getattr(raised.exception, 'retry_after_seconds'),
                        300,
                    )

    async def test_rate_limit_error_carries_bounded_retry_after_ms(
        self,
    ) -> None:
        from src.marketplaces.apify_client import ApifyClient

        cases = (
            ('7', 7_000),
            ('300', 300_000),
            ('99999', 300_000),
            ('Wed, 21 Oct 2026 07:28:00 GMT', None),
        )
        for header, expected_ms in cases:
            with self.subTest(header=header):
                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        429,
                        headers={'Retry-After': header},
                        request=request,
                    )

                client = ApifyClient(
                    make_settings(),
                    client_factory(httpx.MockTransport(handler)),
                )
                with self.assertRaises(MarketplaceSourceError) as raised:
                    await client.run_actor(
                        'ozon',
                        MarketplaceOperation.SEARCH_PRODUCTS,
                        SearchRequest(query='synthetic query', limit=3),
                    )

                self.assertEqual(
                    SourceOutcome.RATE_LIMITED,
                    raised.exception.outcome,
                )
                self.assertEqual(
                    expected_ms,
                    raised.exception.retry_after_ms,
                )

    async def test_rate_limit_without_a_header_has_no_hint(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, request=request)

        client = ApifyClient(
            make_settings(),
            client_factory(httpx.MockTransport(handler)),
        )
        with self.assertRaises(MarketplaceSourceError) as raised:
            await client.run_actor(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                SearchRequest(query='synthetic query', limit=3),
            )

        self.assertEqual(SourceOutcome.RATE_LIMITED, raised.exception.outcome)
        self.assertIsNone(raised.exception.retry_after_ms)

    async def test_invalid_dataset_schema_is_parse_drift(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'unexpected': 'object'},
                                  request=request)

        client = ApifyClient(
            make_settings(),
            client_factory(httpx.MockTransport(handler)),
        )
        with self.assertRaises(MarketplaceSourceError) as raised:
            await client.run_actor(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                SearchRequest(query='synthetic query', limit=3),
            )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, raised.exception.outcome)

    async def test_invalid_json_has_no_raw_exception_chain(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        marker = 'synthetic-invalid-json-sentinel'

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=marker, request=request)

        client = ApifyClient(
            make_settings(),
            client_factory(httpx.MockTransport(handler)),
        )
        with self.assertRaises(MarketplaceSourceError) as raised:
            await client.run_actor(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                SearchRequest(query='synthetic query', limit=3),
            )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, raised.exception.outcome)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn(marker, repr(raised.exception))

    async def test_transport_failure_has_no_raw_exception_chain(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        marker = 'apify-token-sentinel'

        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(marker, request=request)

        client = ApifyClient(
            make_settings(),
            client_factory(httpx.MockTransport(handler)),
        )
        with self.assertRaises(MarketplaceSourceError) as raised:
            await client.run_actor(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                SearchRequest(query='synthetic query', limit=3),
            )

        self.assertEqual(
            SourceOutcome.TRANSPORT_ERROR,
            raised.exception.outcome,
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn(marker, repr(raised.exception))

    async def test_oversized_stream_is_typed_without_retry(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        body = b'[{"id":"940001"}]'
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                stream=_ChunkStream((body[:5], body[5:])),
                request=request,
            )

        settings = make_settings(marketplace_max_content_bytes=len(body) - 1)
        client = ApifyClient(
            settings,
            client_factory(httpx.MockTransport(handler)),
        )
        with self.assertRaises(MarketplaceSourceError) as raised:
            await client.run_actor(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                SearchRequest(query='synthetic query', limit=3),
            )

        self.assertEqual(
            SourceOutcome.TRANSPORT_ERROR,
            raised.exception.outcome,
        )
        self.assertEqual(SafeErrorCode.CONTENT_TOO_LARGE,
                         raised.exception.error_code)
        self.assertEqual(1, calls)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    async def test_stream_at_content_limit_is_parsed(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        body = b'[{"id":"940001"}]'

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_ChunkStream((body[:5], body[5:])),
                request=request,
            )

        settings = make_settings(marketplace_max_content_bytes=len(body))
        client = ApifyClient(
            settings,
            client_factory(httpx.MockTransport(handler)),
        )
        result = await client.run_actor(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            SearchRequest(query='synthetic query', limit=3),
        )

        self.assertEqual([{'id': '940001'}], result)

    async def test_huge_retry_after_is_still_rate_limited(self) -> None:
        from src.marketplaces.apify_client import ApifyClient

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={'Retry-After': '9' * 5000},
                request=request,
            )

        client = ApifyClient(
            make_settings(),
            client_factory(httpx.MockTransport(handler)),
        )
        with self.assertRaises(MarketplaceSourceError) as raised:
            await client.run_actor(
                'ozon',
                MarketplaceOperation.SEARCH_PRODUCTS,
                SearchRequest(query='synthetic query', limit=3),
            )

        self.assertEqual(SourceOutcome.RATE_LIMITED, raised.exception.outcome)
        retry_after = getattr(raised.exception, 'retry_after_seconds')
        self.assertTrue(retry_after is None or retry_after <= 300)


if __name__ == '__main__':
    unittest.main()
