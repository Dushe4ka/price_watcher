from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.browser.profiles import ProfileInUseError
from src.captcha.models import ChallengeResolution
from src.marketplaces.contracts import (
    CategoryRequest,
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.sources.browser import (
    OzonBrowserSource,
    WildberriesBrowserSource,
    YandexMarketBrowserSource,
)
from tests.browser_source_fakes import (
    BrokenEvaluationPage,
    BrokenContentPage,
    FakeCoordinator,
    FakeManager,
    FakePage,
    SequenceEvaluationPage,
    SequencedStatusPage,
    SequencedUrlPage,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'marketplaces'
OZON_API_URL = 'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'

# Captured (truncated) from a real WB navigation returning HTTP 498: WB's own
# "wbaas" self-resolving antibot check, not a third-party CAPTCHA widget.
WB_ANTIBOT_HTML = (
    '<!DOCTYPE html><html lang="ru" data-theme="light">'
    '<head><meta charset="UTF-8">'
    '<script type="module" crossorigin="" '
    'src="/__wbaas/challenges/antibot/__static/v2/index-yz9dDw8g.js">'
    '</script>'
    '<script src="/__wbaas/challenges/antibot/statics/'
    'challenge-solver_v1.0.8.js" async="" type="text/javascript"></script>'
    '</head><body><div class="w"><div id="c_cont"><div id="wait_msg">'
    '<div class="w_i wrapper__item__default">'
    '<p class="wait_msg">Проверяем браузер</p></div><div class="loader">'
    '</div></div></div></div></body></html>'
)

_WB_DETAIL_EVALUATION = {
    'priceCurrent': '750',
    'pageTitle': 'Synthetic WB Item 920001 купить за 750 ₽',
}


def _fixture(path: str) -> str:
    return (FIXTURES / path).read_text(encoding='utf-8')


def _wb_card() -> dict[str, str | None]:
    return {
        'nmId': '920001',
        'title': 'Synthetic WB Item',
        'brand': None,
        'imageUrl': None,
        'priceCurrent': '750',
        'priceOld': None,
        'ratingValue': None,
        'reviewText': None,
    }


class BrowserSourceHappyPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_ozon_product_uses_canonical_widget_mapper(self) -> None:
        page = FakePage(
            html='<html>Ozon</html>',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': OZON_API_URL,
                'body': _fixture('ozon/success.json'),
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual('910001', result.value.external_id)

    async def test_ozon_search_returns_bounded_products(self) -> None:
        page = FakePage(
            html='<html>Ozon</html>',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': OZON_API_URL,
                'body': _fixture('ozon/success.json'),
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        product_ids = tuple(product.external_id for product in result.value)
        self.assertEqual(('910001',), product_ids)

    async def test_ozon_category_uses_trusted_slug_mapping(self) -> None:
        page = FakePage(
            html='<html>Ozon</html>',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': OZON_API_URL,
                'body': _fixture('ozon/success.json'),
            },
        )
        source = OzonBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            category_urls={'synthetic': 'https://www.ozon.ru/category/1/'},
        )

        result = await source.crawl_category(
            CategoryRequest(category_slug='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual(['910001'], result.value.product_ids)

    async def test_wildberries_product_uses_existing_dom_mapper(self) -> None:
        page = FakePage(
            html=_fixture('wildberries/success.html'),
            evaluation={
                'priceCurrent': '750',
                'pageTitle': 'Synthetic WB Item 920001 купить за 750 ₽',
            },
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('920001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual('920001', result.value.external_id)

    async def test_wildberries_search_uses_existing_card_mapper(self) -> None:
        page = FakePage(
            html=_fixture('wildberries/success.html'),
            evaluation=[_wb_card()],
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        product_ids = tuple(product.external_id for product in result.value)
        self.assertEqual(('920001',), product_ids)

    async def test_wildberries_category_uses_trusted_slug_mapping(
        self,
    ) -> None:
        page = FakePage(
            html=_fixture('wildberries/success.html'),
            evaluation=[_wb_card()],
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            category_urls={
                'synthetic': 'https://www.wildberries.ru/catalog/1/',
            },
        )

        result = await source.crawl_category(
            CategoryRequest(category_slug='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual(['920001'], result.value.product_ids)

    async def test_yandex_product_uses_existing_json_ld_parser(self) -> None:
        page = FakePage(html=_fixture('yandex_market/success.html'))
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual('930001', result.value.external_id)

    async def test_yandex_search_returns_bounded_products(self) -> None:
        page = FakePage(html=_fixture('yandex_market/success.html'))
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        product_ids = tuple(product.external_id for product in result.value)
        self.assertEqual(('930001',), product_ids)

    async def test_yandex_category_uses_trusted_slug_mapping(self) -> None:
        page = FakePage(html=_fixture('yandex_market/success.html'))
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            category_urls={
                'synthetic': 'https://market.yandex.ru/catalog--x/1',
            },
        )

        result = await source.crawl_category(
            CategoryRequest(category_slug='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual(['930001'], result.value.product_ids)


class BrowserSourceOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_empty_search_is_empty_for_each_marketplace(
        self,
    ) -> None:
        sources = (
            OzonBrowserSource(
                FakeManager(FakePage(
                    html='<html>Ozon</html>',
                    evaluation={
                        'kind': 'body',
                        'status': 200,
                        'url': OZON_API_URL,
                        'body': _fixture('ozon/empty.json'),
                    },
                )),
                FakeCoordinator(),
            ),
            WildberriesBrowserSource(
                FakeManager(FakePage(
                    html=_fixture('wildberries/empty.html'),
                    evaluation=[],
                )),
                FakeCoordinator(),
            ),
            YandexMarketBrowserSource(
                FakeManager(FakePage(
                    html=_fixture('yandex_market/empty.html'),
                )),
                FakeCoordinator(),
            ),
        )

        for source in sources:
            with self.subTest(source=type(source).__name__):
                result = await source.search_products(
                    SearchRequest(query='synthetic', limit=1),
                )
                self.assertEqual(SourceOutcome.EMPTY, result.outcome)

    async def test_valid_empty_product_is_not_found(self) -> None:
        page = FakePage(html=_fixture('yandex_market/empty.html'))
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.NOT_FOUND, result.outcome)

    async def test_product_http_not_found_is_not_found(self) -> None:
        page = FakePage(html='unused', status=404)
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.NOT_FOUND, result.outcome)

    async def test_fake_empty_shell_is_parse_drift(self) -> None:
        page = FakePage(html=_fixture('yandex_market/drift.html'))
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_initial_interactive_challenge_is_safe_failure(self) -> None:
        page = FakePage(html='unused')
        coordinator = FakeCoordinator(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
        )
        source = YandexMarketBrowserSource(FakeManager(page), coordinator)

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(
            SafeErrorCode.CHALLENGE_UNSUPPORTED,
            result.attempt.error_code,
        )

    async def test_post_fetch_challenge_is_safe_failure(self) -> None:
        page = FakePage(html=_fixture('yandex_market/success.html'))
        coordinator = FakeCoordinator(
            ChallengeResolution.NO_CHALLENGE,
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
        )
        source = YandexMarketBrowserSource(FakeManager(page), coordinator)

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)

    async def test_solved_challenge_still_requires_structural_validation(
        self,
    ) -> None:
        page = FakePage(html=_fixture('yandex_market/drift.html'))
        coordinator = FakeCoordinator(
            ChallengeResolution.SOLVED,
            ChallengeResolution.NO_CHALLENGE,
        )
        source = YandexMarketBrowserSource(FakeManager(page), coordinator)

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_rate_limit_is_explicit(self) -> None:
        page = FakePage(
            html='unused',
            evaluation={
                'kind': 'status',
                'status': 429,
                'url': OZON_API_URL,
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.RATE_LIMITED, result.outcome)
        self.assertEqual(SafeErrorCode.RATE_LIMITED, result.attempt.error_code)

    async def test_wrong_host_ozon_capture_is_rejected(self) -> None:
        page = FakePage(
            html='unused',
            evaluation={
                'kind': 'unsafe_response',
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)

    async def test_ozon_antibot_redirect_is_challenge(self) -> None:
        page = FakePage(
            html='unused',
            evaluation={
                'kind': 'redirect',
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(
            SafeErrorCode.CHALLENGE_DETECTED,
            result.attempt.error_code,
        )

    async def test_closed_page_is_transport_error(self) -> None:
        page = FakePage(html='unused')
        page.closed = True
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(
            SafeErrorCode.TRANSPORT_FAILED,
            result.attempt.error_code,
        )

    async def test_closed_context_failure_is_transport_error(self) -> None:
        manager = FakeManager(
            FakePage(html='unused'),
            lease_error=RuntimeError('synthetic closed context'),
        )
        source = YandexMarketBrowserSource(manager, FakeCoordinator())

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)

    async def test_profile_lock_conflict_is_not_a_generic_transport_error(
        self,
    ) -> None:
        # The lock is acquired lazily inside ``ensure_context()``, so a
        # profile already owned by another process only surfaces here, during
        # a real operation. Each marketplace adapter owns its own lease
        # helper, so all three are checked.
        cases = (
            (OzonBrowserSource, '910001'),
            (WildberriesBrowserSource, '920001'),
            (YandexMarketBrowserSource, '930001'),
        )
        for source_class, product_id in cases:
            with self.subTest(source=source_class.__name__):
                manager = FakeManager(
                    FakePage(html='unused'),
                    lease_error=ProfileInUseError(
                        '/synthetic/profiles/api/ozon is already in use',
                    ),
                )
                source = source_class(manager, FakeCoordinator())

                result = await source.parse_product(
                    ProductRequest(product_id),
                )

                self.assertEqual(
                    SourceOutcome.TRANSPORT_ERROR,
                    result.outcome,
                )
                self.assertEqual(
                    SafeErrorCode.PROFILE_LOCKED,
                    result.attempt.error_code,
                )
                self.assertNotEqual(
                    SafeErrorCode.TRANSPORT_FAILED,
                    result.attempt.error_code,
                )
                self.assertNotIn('/synthetic/profiles', repr(result))

    async def test_malformed_ozon_payload_is_parse_drift(self) -> None:
        page = FakePage(
            html='unused',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': OZON_API_URL,
                'body': '{malformed',
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_content_exception_does_not_escape_or_render_raw_detail(
        self,
    ) -> None:
        page = BrokenContentPage(html='unused')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertNotIn('synthetic-sensitive', repr(result))

    async def test_mapper_exception_is_parse_drift(self) -> None:
        page = FakePage(
            html='<html>Ozon</html>',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': OZON_API_URL,
                'body': _fixture('ozon/success.json'),
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        with patch(
            'src.marketplaces.sources.browser.extract_product_summary_map',
            side_effect=RuntimeError('synthetic mapper drift'),
        ):
            result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_wb_dom_evaluation_error_is_parse_drift(self) -> None:
        page = BrokenEvaluationPage(
            html=_fixture('wildberries/success.html'),
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.search_products(
            SearchRequest(query='synthetic', limit=1),
        )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_navigation_403_is_challenge(self) -> None:
        page = FakePage(
            html=_fixture('yandex_market/challenge.html'),
            status=403,
        )
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)

    async def test_navigation_5xx_is_transport_error(self) -> None:
        page = FakePage(html='synthetic upstream failure', status=503)
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(
            SafeErrorCode.TRANSPORT_FAILED,
            result.attempt.error_code,
        )

    async def test_ozon_solved_post_capture_uses_fresh_capture(self) -> None:
        page = SequenceEvaluationPage(
            html='<html>Ozon</html>',
            evaluations=[
                {
                    'kind': 'body',
                    'status': 200,
                    'url': OZON_API_URL,
                    'body': _fixture('ozon/drift.json'),
                },
                {
                    'kind': 'body',
                    'status': 200,
                    'url': OZON_API_URL,
                    'body': _fixture('ozon/success.json'),
                },
            ],
        )
        coordinator = FakeCoordinator(
            ChallengeResolution.NO_CHALLENGE,
            ChallengeResolution.SOLVED,
            ChallengeResolution.NO_CHALLENGE,
        )
        source = OzonBrowserSource(FakeManager(page), coordinator)

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertEqual(2, len(page.evaluation_pages))


class WildberriesAntibotSelfResolveTests(unittest.IsolatedAsyncioTestCase):
    """WB's self-resolving 'wbaas' antibot check (HTTP 498)."""

    async def test_self_resolving_check_clears_within_deadline(self) -> None:
        page = SequencedStatusPage(
            statuses=[498, 200],
            htmls=[WB_ANTIBOT_HTML, _fixture('wildberries/success.html')],
            evaluation=_WB_DETAIL_EVALUATION,
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            antibot_poll_interval_sec=0.01,
            total_timeout_sec=2.0,
        )

        result = await source.parse_product(ProductRequest('920001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        assert result.value is not None
        self.assertEqual('920001', result.value.external_id)
        self.assertGreaterEqual(len(page.goto_urls), 2)
        self.assertEqual(
            {page.goto_urls[0]},
            set(page.goto_urls),
        )

    async def test_never_resolving_check_is_challenge_not_parse_drift(
        self,
    ) -> None:
        page = SequencedStatusPage(
            statuses=[498],
            htmls=[WB_ANTIBOT_HTML],
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            antibot_poll_interval_sec=0.01,
            total_timeout_sec=0.3,
        )

        result = await source.parse_product(ProductRequest('920001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(
            SafeErrorCode.CHALLENGE_DETECTED,
            result.attempt.error_code,
        )
        self.assertNotEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_unrelated_non_2xx_statuses_are_unaffected(self) -> None:
        cases = (
            (418, SourceOutcome.PARSE_DRIFT, SafeErrorCode.PARSE_DRIFT),
            (
                502,
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            ),
        )
        for status, expected_outcome, expected_error in cases:
            with self.subTest(status=status):
                page = FakePage(
                    html='<html>unexpected upstream response</html>',
                    status=status,
                )
                source = WildberriesBrowserSource(
                    FakeManager(page),
                    FakeCoordinator(),
                )

                result = await source.parse_product(ProductRequest('920001'))

                self.assertEqual(expected_outcome, result.outcome)
                self.assertEqual(expected_error, result.attempt.error_code)
                self.assertEqual(1, len(page.goto_urls))

    async def test_wait_is_bounded_by_shared_deadline_and_does_not_hang(
        self,
    ) -> None:
        page = SequencedStatusPage(statuses=[498], htmls=[WB_ANTIBOT_HTML])
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            antibot_poll_interval_sec=0.01,
            total_timeout_sec=0.3,
        )
        started = asyncio.get_running_loop().time()

        result = await source.parse_product(ProductRequest('920001'))
        elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        # The loop must stop respecting the shared deadline, not run forever:
        # it settles well before an unrelated, much larger bound...
        self.assertLess(elapsed, 1.0)
        # ...yet it did poll (re-navigated) more than once before giving up.
        self.assertGreater(len(page.goto_urls), 1)


class BrowserSourceRuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_runtime_dto_values_are_invalid_config(self) -> None:
        invalid_calls = (
            ('product-bool', 'parse_product', ProductRequest(True)),
            ('product-object', 'parse_product', object()),
            (
                'search-query-bool',
                'search_products',
                SearchRequest(query=True, limit=1),
            ),
            (
                'search-limit-bool',
                'search_products',
                SearchRequest(query='x', limit=True),
            ),
            (
                'search-page-bool',
                'search_products',
                SearchRequest(query='x', limit=1, page=True),
            ),
            (
                'category-slug-int',
                'crawl_category',
                CategoryRequest(category_slug=7, limit=1),
            ),
            (
                'category-limit-bool',
                'crawl_category',
                CategoryRequest(category_slug='trusted', limit=True),
            ),
        )
        for label, method_name, request in invalid_calls:
            with self.subTest(label=label):
                page = FakePage(html='unused')
                source = YandexMarketBrowserSource(
                    FakeManager(page),
                    FakeCoordinator(),
                    category_urls={
                        'trusted': 'https://market.yandex.ru/catalog--x/1',
                    },
                )

                method = getattr(source, method_name)
                result = await method(request)

                self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)
                self.assertEqual([], page.goto_urls)

    async def test_url_race_before_coordinator_prevents_coordinator_call(
        self,
    ) -> None:
        page = SequencedUrlPage(
            html=_fixture('yandex_market/success.html'),
            unsafe_after_reads=1,
        )
        coordinator = FakeCoordinator()
        source = YandexMarketBrowserSource(FakeManager(page), coordinator)

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)
        self.assertEqual([], coordinator.pages)

    async def test_invalid_ozon_capture_shape_is_parse_drift(self) -> None:
        page = FakePage(html='unused', evaluation=['not', 'a', 'mapping'])
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)

    async def test_ozon_payload_is_not_retained_by_result(self) -> None:
        marker = 'synthetic-sensitive-raw-payload'
        body = json.dumps({'unexpectedSyntheticEnvelope': marker})
        page = FakePage(
            html='unused',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': OZON_API_URL,
                'body': body,
            },
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertNotIn(marker, repr(result))


if __name__ == '__main__':
    unittest.main()
