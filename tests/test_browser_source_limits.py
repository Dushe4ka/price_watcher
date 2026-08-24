from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.marketplaces.contracts import (
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.sources.browser import (
    OzonBrowserSource,
    WildberriesBrowserSource,
    YandexMarketBrowserSource,
    _ozon_fetch_expression,
)
from src.ozon.constants import OZON_MOBILE_HEADERS
from tests.browser_source_fakes import (
    CancellationSuppressingEnterManager,
    CancellationSuppressingExitManager,
    FakeCoordinator,
    FakeManager,
    FakeOzonStreamingPage,
    FakePage,
    HangingActionPage,
    HangingClosePage,
    HangingCoordinator,
    HangingContentPage,
    NonClosingManager,
    TimingOutNavigationPage,
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
    async def test_deadline_exhaustion_is_timeout_and_closes_page_later(
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
        # Page closure is best effort and must never block the timeout
        # result, so it settles on the loop after the caller already has its
        # outcome rather than before the call returns.
        for _ in range(100):
            if page.closed:
                break
            await asyncio.sleep(0)
        self.assertTrue(page.closed)

    async def test_navigation_timeout_returns_without_awaiting_page_close(
        self,
    ) -> None:
        page = TimingOutNavigationPage()
        source = YandexMarketBrowserSource(
            NonClosingManager(page),
            FakeCoordinator(),
            total_timeout_sec=5,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()

        result = await source.parse_product(ProductRequest('930001'))
        elapsed = loop.time() - started

        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)
        # The absolute deadline still had ~5s left, so a timeout branch that
        # awaited the wedged close would stall here for the whole remainder.
        self.assertLess(elapsed, 1.0)

        for _ in range(100):
            if page.close_started.is_set():
                break
            await asyncio.sleep(0)
        self.assertTrue(page.close_started.is_set())
        self.assertFalse(page.closed)

        # Closure runs as an independent background task, so it survives the
        # timed-out operation and still completes once the renderer responds.
        page.release_close.set()
        for _ in range(100):
            if page.closed:
                break
            await asyncio.sleep(0)
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

    async def test_hung_lease_enter_is_bounded_by_source_deadline(
        self,
    ) -> None:
        page = FakePage(html=_fixture('yandex_market/success.html'))
        manager = CancellationSuppressingEnterManager(page)
        source = YandexMarketBrowserSource(
            manager,
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )
        started = asyncio.get_running_loop().time()
        operation = asyncio.create_task(
            source.parse_product(ProductRequest('930001')),
        )
        await manager.enter_started.wait()

        done, _ = await asyncio.wait((operation,), timeout=0.15)
        finished_by_deadline = operation in done
        if not finished_by_deadline:
            operation.cancel()
        manager.release.set()
        result = await operation

        self.assertTrue(finished_by_deadline)
        self.assertLess(asyncio.get_running_loop().time() - started, 0.15)
        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)
        self.assertEqual([], page.goto_urls)

    async def test_hung_lease_exit_and_close_do_not_delay_timeout_result(
        self,
    ) -> None:
        page = HangingClosePage(
            html=_fixture('yandex_market/success.html'),
        )
        manager = CancellationSuppressingExitManager(page)
        source = YandexMarketBrowserSource(
            manager,
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )
        started = asyncio.get_running_loop().time()
        operation = asyncio.create_task(
            source.parse_product(ProductRequest('930001')),
        )
        await manager.exit_started.wait()

        done, _ = await asyncio.wait((operation,), timeout=0.15)
        finished_by_deadline = operation in done
        if not finished_by_deadline:
            operation.cancel()
        result = await operation

        self.assertTrue(finished_by_deadline)
        self.assertLess(asyncio.get_running_loop().time() - started, 0.15)
        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)
        self.assertTrue(page.close_started.is_set())
        self.assertFalse(page.closed)
        operational_calls = (
            len(page.goto_urls),
            len(page.content_pages),
            len(page.evaluation_pages),
        )

        manager.release.set()
        page.release_close.set()
        await asyncio.sleep(0)
        self.assertEqual(
            operational_calls,
            (
                len(page.goto_urls),
                len(page.content_pages),
                len(page.evaluation_pages),
            ),
        )

    async def test_goto_timeout_is_typed(self) -> None:
        page = HangingActionPage(action='goto')
        source = YandexMarketBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)

    async def test_dom_evaluate_timeout_is_typed(self) -> None:
        page = HangingActionPage(
            action='evaluate',
            html=_fixture('wildberries/success.html'),
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )

        result = await source.parse_product(ProductRequest('920001'))

        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)

    async def test_ozon_fetch_timeout_is_typed(self) -> None:
        page = HangingActionPage(action='fetch', html='<html>Ozon</html>')
        source = OzonBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            total_timeout_sec=0.01,
        )

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)

    async def test_coordinator_timeout_is_typed(self) -> None:
        page = FakePage(html=_fixture('yandex_market/success.html'))
        source = YandexMarketBrowserSource(
            FakeManager(page),
            HangingCoordinator(),
            total_timeout_sec=0.01,
        )

        result = await source.parse_product(ProductRequest('930001'))

        self.assertEqual(SafeErrorCode.TIMEOUT, result.attempt.error_code)


class OzonStreamingCaptureTests(unittest.IsolatedAsyncioTestCase):
    def _success_body(self) -> str:
        return (FIXTURES / 'ozon' / 'success.json').read_text(
            encoding='utf-8',
        )

    async def test_manual_redirect_does_not_read_body(self) -> None:
        page = FakeOzonStreamingPage(
            body=self._success_body(),
            redirected=True,
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertTrue(page.manual_redirect_requested)
        self.assertEqual(0, page.body_reads)

    async def test_opaque_redirect_does_not_read_body(self) -> None:
        page = FakeOzonStreamingPage(
            body=self._success_body(),
            response_type='opaqueredirect',
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.CHALLENGE, result.outcome)
        self.assertEqual(0, page.body_reads)

    async def test_wrong_host_does_not_read_body(self) -> None:
        page = FakeOzonStreamingPage(
            body=self._success_body(),
            response_url='https://attacker.invalid/payload.json',
        )
        source = OzonBrowserSource(FakeManager(page), FakeCoordinator())

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.INVALID_CONFIG, result.outcome)
        self.assertEqual(0, page.body_reads)

    async def test_stream_aborts_immediately_after_byte_limit(self) -> None:
        body = self._success_body()
        encoded = body.encode('utf-8')
        page = FakeOzonStreamingPage(
            body=body,
            chunks=[encoded, b'x', b'unread'],
        )
        source = OzonBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=len(encoded),
        )

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(
            SafeErrorCode.CONTENT_TOO_LARGE,
            result.attempt.error_code,
        )
        self.assertTrue(page.streaming_reader_requested)
        self.assertTrue(page.reader_cancelled)
        self.assertEqual(2, page.body_reads)

    async def test_stream_at_exact_byte_limit_is_accepted(self) -> None:
        body = json.dumps(json.loads(self._success_body()), ensure_ascii=False)
        page = FakeOzonStreamingPage(body=body)
        source = OzonBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=len(body.encode('utf-8')),
        )

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)
        self.assertEqual(1, page.body_reads)


class EvaluateResultLimitTests(unittest.IsolatedAsyncioTestCase):
    """`page.evaluate()` results are capped like HTML and captured bodies."""

    @staticmethod
    def _cards(count: int) -> list[dict[str, str]]:
        return [
            {
                'nmId': str(920001 + index),
                'title': 'Synthetic WB Item ' + 'x' * 256,
                'priceCurrent': '750',
            }
            for index in range(count)
        ]

    async def test_oversized_wb_dom_result_is_rejected_before_mapping(
        self,
    ) -> None:
        page = FakePage(
            html=_fixture('wildberries/success.html'),
            evaluation=self._cards(400),
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=4096,
        )

        result = await source.search_products(
            SearchRequest(query='кот', limit=5),
        )

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual(
            SafeErrorCode.CONTENT_TOO_LARGE,
            result.attempt.error_code,
        )

    async def test_same_wb_dom_result_succeeds_under_a_larger_cap(
        self,
    ) -> None:
        page = FakePage(
            html=_fixture('wildberries/success.html'),
            evaluation=self._cards(400),
        )
        source = WildberriesBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=2_000_000,
        )

        result = await source.search_products(
            SearchRequest(query='кот', limit=5),
        )

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)

    async def test_oversized_ozon_capture_envelope_is_rejected(self) -> None:
        # The body itself is tiny, so only an envelope-level cap can reject
        # this: without one the padding sails straight into _decode_capture.
        page = FakePage(
            html='<html>Ozon</html>',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': (
                    'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'
                ),
                'body': _fixture('ozon/success.json'),
                'padding': ['x' * 1024] * 64,
            },
        )
        source = OzonBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=4096,
        )

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.PARSE_DRIFT, result.outcome)
        self.assertEqual(
            SafeErrorCode.CONTENT_TOO_LARGE,
            result.attempt.error_code,
        )

    async def test_ozon_capture_envelope_fits_a_body_at_the_exact_cap(
        self,
    ) -> None:
        body = _fixture('ozon/success.json')
        page = FakePage(
            html='<html>Ozon</html>',
            evaluation={
                'kind': 'body',
                'status': 200,
                'url': (
                    'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'
                ),
                'body': body,
            },
        )
        source = OzonBrowserSource(
            FakeManager(page),
            FakeCoordinator(),
            max_content_bytes=len(body.encode('utf-8')),
        )

        result = await source.parse_product(ProductRequest('910001'))

        self.assertEqual(SourceOutcome.SUCCESS, result.outcome)


NODE = shutil.which('node')

_NODE_DRIVER = """'use strict';

const capture = __CAPTURE__;

function makeResponse(spec, probe) {
  const encoder = new TextEncoder();
  let chunks = [];
  if (spec.byteChunks) {
    chunks = spec.byteChunks.map((item) => Uint8Array.from(item));
  } else if (spec.chunks) {
    chunks = spec.chunks.map((item) => encoder.encode(item));
  }
  let index = 0;
  const body = spec.hasBody === false ? null : {
    getReader() {
      probe.readerCreated = true;
      return {
        async read() {
          if (index >= chunks.length) {
            return {done: true, value: undefined};
          }
          probe.chunksRead += 1;
          return {done: false, value: chunks[index++]};
        },
        async cancel() {
          probe.cancelled = true;
        },
      };
    },
  };
  return {
    redirected: spec.redirected === true,
    type: spec.type === undefined ? 'basic' : spec.type,
    status: spec.status === undefined ? 200 : spec.status,
    url: spec.url === undefined ? '' : spec.url,
    body: body,
  };
}

async function main() {
  const scenarios = JSON.parse(process.argv[2]);
  const results = [];
  for (const scenario of scenarios) {
    const probe = {
      readerCreated: false,
      chunksRead: 0,
      cancelled: false,
      requestUrl: null,
      redirect: null,
      credentials: null,
      headerNames: [],
    };
    globalThis.fetch = async (url, init) => {
      probe.requestUrl = url;
      probe.redirect = init.redirect;
      probe.credentials = init.credentials;
      probe.headerNames = Object.keys(init.headers || {});
      return makeResponse(scenario.response, probe);
    };
    let outcome;
    try {
      outcome = await capture();
    } catch (error) {
      outcome = {kind: 'threw', message: String(error && error.message)};
    }
    results.push({name: scenario.name, outcome: outcome, probe: probe});
  }
  process.stdout.write(JSON.stringify(results));
}

main();
"""


@unittest.skipUnless(
    NODE,
    'node is required to execute the in-page Ozon fetch JavaScript',
)
class OzonFetchExpressionContractTests(unittest.TestCase):
    """Execute the generated in-page fetch JS against a stubbed Response.

    This drives the real control flow of the expression that ships to the
    renderer - `redirect: 'manual'` handling, the pre-body URL boundary check
    and the incremental reader - rather than a Python fake returning a
    scripted dict.
    """

    API_URL = 'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=%2F'

    def _run(
        self,
        scenarios: list[dict[str, object]],
        max_bytes: int,
    ) -> dict[str, dict[str, Any]]:
        expression = _ozon_fetch_expression(self.API_URL, max_bytes)
        driver = _NODE_DRIVER.replace('__CAPTURE__', expression)
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'ozon_capture_contract.mjs'
            script.write_text(driver, encoding='utf-8')
            completed = subprocess.run(
                [str(NODE), str(script), json.dumps(scenarios)],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        return {
            row['name']: row
            for row in json.loads(completed.stdout)
        }

    def test_request_is_manual_redirect_without_forbidden_headers(
        self,
    ) -> None:
        rows = self._run(
            [{
                'name': 'ok',
                'response': {
                    'status': 200,
                    'url': self.API_URL,
                    'chunks': ['{}'],
                },
            }],
            2_000_000,
        )

        probe = rows['ok']['probe']
        self.assertEqual('manual', probe['redirect'])
        self.assertEqual('include', probe['credentials'])
        lowered = [name.lower() for name in probe['headerNames']]
        self.assertNotIn('user-agent', lowered)
        self.assertIn('x-o3-app-name', lowered)
        # The source constant still carries the UA; only the in-page fetch
        # drops it, because the Fetch spec forbids setting it there.
        self.assertIn('User-Agent', OZON_MOBILE_HEADERS)

    def test_redirects_are_typed_without_reading_any_body(self) -> None:
        scenarios = [
            {
                'name': 'opaqueredirect',
                'response': {
                    'type': 'opaqueredirect',
                    'status': 0,
                    'url': '',
                    'chunks': ['{"never":"read"}'],
                },
            },
            {
                'name': 'redirected_flag',
                'response': {
                    'redirected': True,
                    'status': 200,
                    'url': self.API_URL,
                    'chunks': ['{"never":"read"}'],
                },
            },
            {
                'name': 'status_307',
                'response': {
                    'status': 307,
                    'url': self.API_URL,
                    'chunks': ['{"never":"read"}'],
                },
            },
        ]

        rows = self._run(scenarios, 2_000_000)

        for scenario in scenarios:
            name = scenario['name']
            with self.subTest(scenario=name):
                self.assertEqual('redirect', rows[name]['outcome']['kind'])
                self.assertFalse(rows[name]['probe']['readerCreated'])
                self.assertEqual(0, rows[name]['probe']['chunksRead'])

    def test_unsafe_final_urls_are_typed_before_the_body_is_touched(
        self,
    ) -> None:
        unsafe_urls = (
            'https://www.ozon.ru.attacker.invalid/page.json',
            'https://user@www.ozon.ru/page.json',
            'https://user:pass@www.ozon.ru/page.json',
            'https://127.0.0.1/page.json',
            'https://www.ozon.ru:444/page.json',
            'http://www.ozon.ru/page.json',
            'https://attacker.invalid/page.json',
        )
        scenarios = [
            {
                'name': url,
                'response': {
                    'status': 200,
                    'url': url,
                    'chunks': ['{"never":"read"}'],
                },
            }
            for url in unsafe_urls
        ]

        rows = self._run(scenarios, 2_000_000)

        for url in unsafe_urls:
            with self.subTest(url=url):
                self.assertEqual(
                    'unsafe_response',
                    rows[url]['outcome']['kind'],
                )
                self.assertFalse(rows[url]['probe']['readerCreated'])

    def test_non_success_status_is_typed_before_the_body_is_touched(
        self,
    ) -> None:
        rows = self._run(
            [{
                'name': 'rate_limited',
                'response': {
                    'status': 429,
                    'url': self.API_URL,
                    'chunks': ['{"never":"read"}'],
                },
            }],
            2_000_000,
        )

        outcome = rows['rate_limited']['outcome']
        self.assertEqual('status', outcome['kind'])
        self.assertEqual(429, outcome['status'])
        self.assertNotIn('body', outcome)
        self.assertFalse(rows['rate_limited']['probe']['readerCreated'])

    def test_stream_cancels_on_the_first_overflowing_chunk(self) -> None:
        rows = self._run(
            [{
                'name': 'over_limit',
                'response': {
                    'status': 200,
                    'url': self.API_URL,
                    'byteChunks': [
                        list('кот'.encode('utf-8')),
                        list(b'x'),
                        list(b'never-read'),
                    ],
                },
            }],
            6,
        )

        probe = rows['over_limit']['probe']
        self.assertEqual('too_large', rows['over_limit']['outcome']['kind'])
        self.assertTrue(probe['cancelled'])
        self.assertEqual(2, probe['chunksRead'])

    def test_stream_at_the_exact_cap_decodes_split_multibyte_text(
        self,
    ) -> None:
        encoded = 'кот'.encode('utf-8')
        rows = self._run(
            [
                {
                    'name': 'exact_single_chunk',
                    'response': {
                        'status': 200,
                        'url': self.API_URL,
                        'byteChunks': [list(encoded)],
                    },
                },
                {
                    'name': 'exact_split_chunks',
                    'response': {
                        'status': 200,
                        'url': self.API_URL,
                        'byteChunks': [
                            list(encoded[:3]),
                            list(encoded[3:]),
                        ],
                    },
                },
            ],
            len(encoded),
        )

        for name in ('exact_single_chunk', 'exact_split_chunks'):
            with self.subTest(scenario=name):
                outcome = rows[name]['outcome']
                self.assertEqual('body', outcome['kind'])
                self.assertEqual('кот', outcome['body'])
                self.assertFalse(rows[name]['probe']['cancelled'])

    def test_invalid_utf8_and_missing_body_are_typed(self) -> None:
        rows = self._run(
            [
                {
                    'name': 'invalid_utf8',
                    'response': {
                        'status': 200,
                        'url': self.API_URL,
                        'byteChunks': [[0xFF, 0xFE]],
                    },
                },
                {
                    'name': 'no_body',
                    'response': {
                        'status': 200,
                        'url': self.API_URL,
                        'hasBody': False,
                    },
                },
            ],
            2_000_000,
        )

        self.assertEqual(
            'invalid_encoding',
            rows['invalid_utf8']['outcome']['kind'],
        )
        self.assertTrue(rows['invalid_utf8']['probe']['cancelled'])
        self.assertEqual(
            'invalid_encoding',
            rows['no_body']['outcome']['kind'],
        )
        self.assertFalse(rows['no_body']['probe']['readerCreated'])


if __name__ == '__main__':
    unittest.main()
