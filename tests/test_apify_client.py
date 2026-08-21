from __future__ import annotations

import json
import unittest

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


if __name__ == '__main__':
    unittest.main()
