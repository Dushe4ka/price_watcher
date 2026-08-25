"""The live marketplace probe stays inert and bounded unless gated on."""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch

from scripts import live_marketplace_probe as probe_module
from scripts.live_marketplace_probe import (
    DEFAULT_SERVICE_FACTORY,
    EXIT_DISABLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    LIVE_GATE_ENV,
    LiveProbeDisabled,
    ProbeInputError,
    assert_live_tests_enabled,
    main,
    parse_marketplace,
    parse_operation,
    run_one_probe,
)
from src.marketplaces.contracts import (
    MarketplaceOperation,
    MarketplaceResult,
    SourceAttempt,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode


SECRET = 'sentinel-secret'
GATE_ON = {LIVE_GATE_ENV: '1'}


class RecordingService:
    """A stand-in service that records requests and never touches a network."""

    def __init__(self, outcome: SourceOutcome = SourceOutcome.SUCCESS) -> None:
        self.requests: list[Any] = []
        self._outcome = outcome

    async def crawl_category(self, request: Any) -> MarketplaceResult[Any]:
        self.requests.append(request)
        return self._result(MarketplaceOperation.CRAWL_CATEGORY)

    async def parse_product(self, request: Any) -> MarketplaceResult[Any]:
        self.requests.append(request)
        return self._result(MarketplaceOperation.PARSE_PRODUCT)

    async def search_products(self, request: Any) -> MarketplaceResult[Any]:
        self.requests.append(request)
        return self._result(MarketplaceOperation.SEARCH_PRODUCTS)

    def _result(
        self,
        operation: MarketplaceOperation,
    ) -> MarketplaceResult[Any]:
        success = self._outcome is SourceOutcome.SUCCESS
        return MarketplaceResult(
            marketplace='ozon',
            operation=operation,
            outcome=self._outcome,
            value=(f'{SECRET}-payload',) if success else None,
            attempts=(
                SourceAttempt(
                    source=SourceName.BROWSER,
                    outcome=self._outcome,
                    duration_ms=7,
                    item_count=1 if success else 0,
                    error_code=(
                        None
                        if self._outcome
                        in (SourceOutcome.SUCCESS, SourceOutcome.EMPTY)
                        else SafeErrorCode.TRANSPORT_FAILED
                    ),
                ),
            ),
            selected_source=SourceName.BROWSER,
        )


class ExplodingFactory:
    """A service factory that fails the test if the probe ever composes one."""

    def __init__(self, case: unittest.TestCase) -> None:
        self._case = case

    def __call__(self, marketplace: str) -> Any:
        self._case.fail('probe composed a live service without the gate')


class GateTests(unittest.TestCase):
    def test_live_probe_requires_explicit_gate(self) -> None:
        with self.assertRaises(LiveProbeDisabled):
            assert_live_tests_enabled({})

    def test_unset_and_falsy_gate_values_stay_disabled(self) -> None:
        for value in ('', '0', 'false', 'no', 'yes', 'true', ' 1'):
            with self.subTest(value=value):
                with self.assertRaises(LiveProbeDisabled):
                    assert_live_tests_enabled({LIVE_GATE_ENV: value})

    def test_explicit_gate_value_enables_the_probe(self) -> None:
        assert_live_tests_enabled(GATE_ON)

    def test_gate_message_names_the_variable_only(self) -> None:
        with self.assertRaises(LiveProbeDisabled) as raised:
            assert_live_tests_enabled({LIVE_GATE_ENV: SECRET})
        message = str(raised.exception)
        self.assertIn(LIVE_GATE_ENV, message)
        self.assertNotIn(SECRET, message)


class ParseTests(unittest.TestCase):
    def test_parse_marketplace_accepts_known_marketplaces(self) -> None:
        self.assertEqual('ozon', parse_marketplace('ozon'))
        self.assertEqual('wildberries', parse_marketplace('  Wildberries '))

    def test_parse_marketplace_rejects_unknown_input(self) -> None:
        with self.assertRaises(ValueError) as raised:
            parse_marketplace(SECRET)
        self.assertNotIn(SECRET, str(raised.exception))

    def test_parse_operation_accepts_known_operations(self) -> None:
        self.assertIs(
            MarketplaceOperation.SEARCH_PRODUCTS,
            parse_operation('search_products'),
        )

    def test_parse_operation_rejects_unknown_input(self) -> None:
        with self.assertRaises(ValueError) as raised:
            parse_operation(SECRET)
        self.assertNotIn(SECRET, str(raised.exception))


class RunOneProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_probe_forces_page_one_and_small_limit(self) -> None:
        service = RecordingService()
        lines: list[str] = []
        code = await run_one_probe(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            query=f'{SECRET}-query',
            service_factory=lambda marketplace: service,
            writer=lines.append,
        )
        self.assertEqual(EXIT_OK, code)
        self.assertEqual(1, len(service.requests))
        request = service.requests[0]
        self.assertEqual(1, request.page)
        self.assertLessEqual(request.limit, 3)
        self.assertNotIn(SECRET, '\n'.join(lines))

    async def test_category_probe_uses_a_small_limit(self) -> None:
        service = RecordingService()
        lines: list[str] = []
        code = await run_one_probe(
            'wildberries',
            MarketplaceOperation.CRAWL_CATEGORY,
            category_slug='beauty',
            service_factory=lambda marketplace: service,
            writer=lines.append,
        )
        self.assertEqual(EXIT_OK, code)
        self.assertLessEqual(service.requests[0].limit, 3)

    async def test_probe_prints_only_allowlisted_fields(self) -> None:
        service = RecordingService()
        lines: list[str] = []
        await run_one_probe(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            query=f'{SECRET}-query',
            service_factory=lambda marketplace: service,
            writer=lines.append,
        )
        output = '\n'.join(lines)
        self.assertNotIn(SECRET, output)
        self.assertIn('outcome=success', output)
        self.assertIn('source=browser', output)
        self.assertIn('item_count=1', output)

    async def test_valid_empty_outcome_still_exits_zero(self) -> None:
        service = RecordingService(SourceOutcome.EMPTY)
        code = await run_one_probe(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            query='probe',
            service_factory=lambda marketplace: service,
            writer=lambda line: None,
        )
        self.assertEqual(EXIT_OK, code)

    async def test_failed_outcome_exits_non_zero(self) -> None:
        service = RecordingService(SourceOutcome.CHALLENGE)
        code = await run_one_probe(
            'ozon',
            MarketplaceOperation.SEARCH_PRODUCTS,
            query='probe',
            service_factory=lambda marketplace: service,
            writer=lambda line: None,
        )
        self.assertEqual(EXIT_FAILED, code)

    async def test_direct_call_still_refuses_to_compose_a_live_chain(
        self,
    ) -> None:
        # ``crawl_category`` with no slug is the interesting case: defaulting
        # the slug reads the categories configuration, which imports
        # application settings, so it must not run before the gate either.
        operations = (
            MarketplaceOperation.SEARCH_PRODUCTS,
            MarketplaceOperation.CRAWL_CATEGORY,
        )
        for operation in operations:
            with self.subTest(operation=operation.value):
                lines: list[str] = []
                original = os.environ.pop(LIVE_GATE_ENV, None)
                try:
                    code = await run_one_probe(
                        'ozon',
                        operation,
                        query='probe',
                        writer=lines.append,
                    )
                finally:
                    if original is not None:
                        os.environ[LIVE_GATE_ENV] = original
                self.assertEqual(EXIT_DISABLED, code)
                self.assertIn(LIVE_GATE_ENV, '\n'.join(lines))

    async def test_gate_precedes_any_configuration_load(self) -> None:
        """An ungated probe must not build a request, let alone read config."""
        def exploding_build_request(*args: Any, **kwargs: Any) -> Any:
            self.fail('probe built a request before checking the live gate')

        def exploding_slug(marketplace: str) -> Any:
            self.fail('probe loaded configuration before checking the gate')

        lines: list[str] = []
        original = os.environ.pop(LIVE_GATE_ENV, None)
        try:
            with (
                patch.object(
                    probe_module,
                    '_build_request',
                    exploding_build_request,
                ),
                patch.object(
                    probe_module,
                    '_first_configured_slug',
                    exploding_slug,
                ),
            ):
                code = await run_one_probe(
                    'ozon',
                    MarketplaceOperation.CRAWL_CATEGORY,
                    writer=lines.append,
                )
        finally:
            if original is not None:
                os.environ[LIVE_GATE_ENV] = original
        self.assertEqual(EXIT_DISABLED, code)
        self.assertIn(LIVE_GATE_ENV, '\n'.join(lines))

    async def test_config_failure_is_rejected_without_its_message(
        self,
    ) -> None:
        """A pydantic ``ValidationError`` is a ``ValueError`` carrying env."""
        def leaking_slug(marketplace: str) -> Any:
            raise ValueError(
                f'1 validation error for Settings\n  input_value={SECRET}',
            )

        lines: list[str] = []
        with patch.object(
            probe_module,
            '_first_configured_slug',
            leaking_slug,
        ):
            code = await run_one_probe(
                'ozon',
                MarketplaceOperation.CRAWL_CATEGORY,
                service_factory=ExplodingFactory(self),
                writer=lines.append,
            )
        output = '\n'.join(lines)
        self.assertEqual(EXIT_USAGE, code)
        self.assertNotIn(SECRET, output)
        self.assertIn('ValueError', output)

    async def test_curated_usage_messages_are_still_printed(self) -> None:
        lines: list[str] = []
        code = await run_one_probe(
            'ozon',
            MarketplaceOperation.PARSE_PRODUCT,
            service_factory=ExplodingFactory(self),
            writer=lines.append,
        )
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn('--product-id', '\n'.join(lines))

    def test_curated_probe_input_errors_are_value_errors(self) -> None:
        self.assertTrue(issubclass(ProbeInputError, ValueError))
        with self.assertRaises(ProbeInputError):
            parse_marketplace(SECRET)
        with self.assertRaises(ProbeInputError):
            parse_operation(SECRET)

    async def test_parse_probe_requires_a_product_id(self) -> None:
        code = await run_one_probe(
            'ozon',
            MarketplaceOperation.PARSE_PRODUCT,
            service_factory=ExplodingFactory(self),
            writer=lambda line: None,
        )
        self.assertEqual(EXIT_USAGE, code)


class MainTests(unittest.TestCase):
    def test_main_without_gate_exits_non_zero_and_stays_offline(self) -> None:
        lines: list[str] = []
        code = main(
            ['--marketplace', 'ozon', '--operation', 'search_products'],
            env={},
            service_factory=ExplodingFactory(self),
            writer=lines.append,
        )
        self.assertNotEqual(EXIT_OK, code)
        self.assertEqual(EXIT_DISABLED, code)
        output = '\n'.join(lines)
        self.assertIn(LIVE_GATE_ENV, output)
        self.assertNotIn(SECRET, output)

    def test_default_factory_is_composed_lazily_behind_the_gate(self) -> None:
        self.assertIsNone(DEFAULT_SERVICE_FACTORY)

    def test_ungated_run_refuses_with_the_real_default_factory(self) -> None:
        lines: list[str] = []
        code = main(
            ['--marketplace', 'ozon', '--operation', 'crawl_category'],
            env={},
            writer=lines.append,
        )
        self.assertEqual(EXIT_DISABLED, code)
        self.assertIn(LIVE_GATE_ENV, '\n'.join(lines))

    def test_main_rejects_an_unknown_marketplace_before_any_call(self) -> None:
        lines: list[str] = []
        code = main(
            ['--marketplace', SECRET, '--operation', 'search_products'],
            env=dict(GATE_ON),
            service_factory=ExplodingFactory(self),
            writer=lines.append,
        )
        self.assertEqual(EXIT_USAGE, code)
        self.assertNotIn(SECRET, '\n'.join(lines))

    def test_main_input_rejection_hides_raw_exception_messages(self) -> None:
        """``parse_marketplace`` is not the only source of a ``ValueError``."""
        def leaking_parse(value: str) -> Any:
            raise ValueError(
                f'1 validation error for Settings\n  input_value={SECRET}',
            )

        lines: list[str] = []
        with patch.object(probe_module, 'parse_marketplace', leaking_parse):
            code = main(
                ['--marketplace', 'ozon', '--operation', 'search_products'],
                env=dict(GATE_ON),
                service_factory=ExplodingFactory(self),
                writer=lines.append,
            )
        output = '\n'.join(lines)
        self.assertEqual(EXIT_USAGE, code)
        self.assertNotIn(SECRET, output)
        self.assertIn('ValueError', output)

    def test_main_still_names_the_valid_options_on_a_bad_input(self) -> None:
        lines: list[str] = []
        code = main(
            ['--marketplace', SECRET, '--operation', 'search_products'],
            env=dict(GATE_ON),
            service_factory=ExplodingFactory(self),
            writer=lines.append,
        )
        output = '\n'.join(lines)
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn('wildberries', output)
        self.assertNotIn(SECRET, output)

    def test_main_runs_one_bounded_probe_when_gated_on(self) -> None:
        service = RecordingService()
        lines: list[str] = []
        code = main(
            [
                '--marketplace',
                'ozon',
                '--operation',
                'search_products',
                '--query',
                f'{SECRET}-query',
            ],
            env=dict(GATE_ON),
            service_factory=lambda marketplace: service,
            writer=lines.append,
        )
        self.assertEqual(EXIT_OK, code)
        self.assertEqual(1, len(service.requests))
        self.assertEqual(1, service.requests[0].page)
        self.assertNotIn(SECRET, '\n'.join(lines))


class SmokeScriptTests(unittest.TestCase):
    def test_smoke_scripts_are_gated_and_bounded(self) -> None:
        from scripts import (
            smoke_ozon_crawl,
            smoke_wb_crawl,
            smoke_yandex_market_crawl,
        )

        modules = (
            (smoke_ozon_crawl, 'ozon'),
            (smoke_wb_crawl, 'wildberries'),
            (smoke_yandex_market_crawl, 'yandex_market'),
        )
        for module, marketplace in modules:
            with self.subTest(marketplace=marketplace):
                self.assertEqual(marketplace, module.MARKETPLACE)
                lines: list[str] = []
                code = module.main(
                    [],
                    env={},
                    service_factory=ExplodingFactory(self),
                    writer=lines.append,
                )
                self.assertEqual(EXIT_DISABLED, code)
                self.assertIn(LIVE_GATE_ENV, '\n'.join(lines))


if __name__ == '__main__':
    unittest.main()
