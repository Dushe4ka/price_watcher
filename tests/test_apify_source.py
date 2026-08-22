from __future__ import annotations

import unittest

import httpx

from src.core.config import Settings
from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode


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
        'apify_ozon_crawl_category_actor_id': 'synthetic-category-actor',
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_source(
    *,
    token: str | None = 'apify-token-sentinel',
    search_actor_id: str = 'synthetic-search-actor',
    status_code: int = 200,
    payload: object | None = None,
):
    from src.marketplaces.apify_client import ApifyClient
    from src.marketplaces.sources.apify import ApifySource

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json=[] if payload is None else payload,
            request=request,
        )

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    settings = make_settings(
        apify_token='' if token is None else token,
        apify_ozon_search_products_actor_id=search_actor_id,
    )
    return ApifySource('ozon', ApifyClient(settings, client_factory))


_PRODUCT = {
    'id': '940001',
    'title': 'Synthetic Ozon product',
    'price': '1299.00',
    'originalPrice': '1599.00',
    'inStock': True,
    'imageUrl': 'https://images.example.invalid/940001.jpg',
    'rating': 4.8,
    'reviewCount': 12,
}


class ApifySourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_returns_disabled(self) -> None:
        result = await make_source(token=None).search_products(
            SearchRequest(query='synthetic', limit=2),
        )

        self.assertEqual(SourceOutcome.DISABLED, result.outcome)
        self.assertIsNone(result.value)

    async def test_missing_operation_actor_returns_disabled(self) -> None:
        source = make_source(search_actor_id='')

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )

        self.assertEqual(SourceOutcome.DISABLED, result.outcome)

    async def test_search_maps_synthetic_provider_payload(self) -> None:
        source = make_source(payload=[_PRODUCT])

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=2),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual(
            ('940001',),
            tuple(product.external_id for product in result.value),
        )
        self.assertEqual(1, result.attempt.item_count)
        self.assertEqual('https://www.ozon.ru/product/940001/',
                         result.value[0].product_url)

    async def test_empty_dataset_is_empty(self) -> None:
        result = await make_source(payload=[]).search_products(
            SearchRequest(query='synthetic', limit=2),
        )

        self.assertEqual(SourceOutcome.EMPTY, result.outcome)

    async def test_empty_product_dataset_is_empty(self) -> None:
        result = await make_source(payload=[]).parse_product(
            ProductRequest('940001'),
        )

        self.assertEqual(SourceOutcome.EMPTY, result.outcome)

    async def test_category_maps_synthetic_provider_payload(self) -> None:
        result = await make_source(payload=[_PRODUCT]).crawl_category(
            CategoryRequest(category_slug='electronics', limit=2),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual(['940001'], result.value.product_ids)
        self.assertIn('940001', result.value.pre_parsed)

    async def test_client_errors_are_mapped_without_source_retry(self) -> None:
        expected = (
            (401, SourceOutcome.AUTH_ERROR, SafeErrorCode.AUTH_FAILED),
            (403, SourceOutcome.AUTH_ERROR, SafeErrorCode.AUTH_FAILED),
            (429, SourceOutcome.RATE_LIMITED, SafeErrorCode.RATE_LIMITED),
            (500, SourceOutcome.TRANSPORT_ERROR,
             SafeErrorCode.TRANSPORT_FAILED),
        )
        for status, outcome, error_code in expected:
            with self.subTest(status=status):
                result = await make_source(status_code=status).search_products(
                    SearchRequest(query='synthetic', limit=2),
                )

                self.assertEqual(outcome, result.outcome)
                self.assertEqual(error_code, result.attempt.error_code)

    async def test_transport_failure_is_safe_and_is_not_retried(self) -> None:
        from src.marketplaces.apify_client import ApifyClient
        from src.marketplaces.sources.apify import ApifySource

        calls = 0
        token = 'apify-token-sentinel'

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError(token, request=request)

        def client_factory(**kwargs: object) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

        source = ApifySource(
            'ozon',
            ApifyClient(make_settings(), client_factory),
        )
        with self.assertNoLogs('src.marketplaces', level='INFO'):
            result = await source.search_products(
                SearchRequest(query='synthetic', limit=2),
            )

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(SafeErrorCode.TRANSPORT_FAILED,
                         result.attempt.error_code)
        self.assertEqual(1, calls)
        self.assertNotIn(token, repr(result))

    async def test_invalid_item_schema_is_parse_drift(self) -> None:
        result = await make_source(payload=[{'id': '940001'}]).search_products(
            SearchRequest(query='synthetic', limit=2),
        )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual(SafeErrorCode.PARSE_DRIFT, result.attempt.error_code)

    async def test_nonfinite_and_overflow_numeric_values_are_parse_drift(
        self,
    ) -> None:
        from src.marketplaces.sources.apify import ApifySource

        class DatasetClient:
            def __init__(self, dataset: list[dict[str, object]]) -> None:
                self._dataset = dataset

            def is_enabled(
                self,
                marketplace: str,
                operation: MarketplaceOperation,
            ) -> bool:
                return True

            async def run_actor(
                self,
                marketplace: str,
                operation: MarketplaceOperation,
                request: object,
            ) -> list[dict[str, object]]:
                return self._dataset

        malformed_values = (
            ('infinite_rating', {'rating': float('inf')}),
            ('overflow_rating', {'rating': 10 ** 10_000}),
            ('infinite_price', {'price': float('inf')}),
        )
        for name, override in malformed_values:
            with self.subTest(name=name):
                payload = [dict(_PRODUCT, **override)]
                source = ApifySource('ozon', DatasetClient(payload))
                result = await source.search_products(
                    SearchRequest(query='synthetic', limit=2),
                )

                self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
                self.assertEqual(
                    SafeErrorCode.PARSE_DRIFT,
                    result.attempt.error_code,
                )


if __name__ == '__main__':
    unittest.main()
