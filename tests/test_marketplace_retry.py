from __future__ import annotations

import unittest

from src.marketplaces.contracts import (
    SourceName,
    SourceOutcome,
    SourceResult,
    source_empty,
    source_failure,
    source_success,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.fallback import SourceCall
from src.marketplaces.retry import (
    OperationDeadline,
    RetryPolicy,
    SourceRetryExecutor,
)


class SourceRetryExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_error_is_retried_once_with_bounded_delay(
        self,
    ) -> None:
        calls = 0
        delays: list[float] = []

        async def transport_failure() -> SourceResult[None]:
            nonlocal calls
            calls += 1
            return source_failure(
                SourceName.PUBLIC,
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

        async def sleep(delay: float) -> None:
            delays.append(delay)

        result = await SourceRetryExecutor().run(
            SourceCall(SourceName.PUBLIC, transport_failure),
            RetryPolicy(),
            sleep,
            lambda: 0.0,
            _deadline(),
        )

        self.assertEqual(2, calls)
        self.assertEqual([0.25], delays)
        self.assertEqual(2, result.attempt.transport_attempts)

    async def test_rate_limited_error_is_retried_once(self) -> None:
        calls = 0

        async def rate_limited() -> SourceResult[None]:
            nonlocal calls
            calls += 1
            return source_failure(
                SourceName.APIFY,
                SourceOutcome.RATE_LIMITED,
                SafeErrorCode.RATE_LIMITED,
            )

        result = await SourceRetryExecutor().run(
            SourceCall(SourceName.APIFY, rate_limited),
            RetryPolicy(),
            _no_sleep,
            lambda: 0.0,
            _deadline(),
        )

        self.assertEqual(2, calls)
        self.assertEqual(2, result.attempt.transport_attempts)

    async def test_expired_deadline_prevents_the_retry_sleep_and_call(
        self,
    ) -> None:
        calls = 0
        delays: list[float] = []

        async def transport_failure() -> SourceResult[None]:
            nonlocal calls
            calls += 1
            return source_failure(
                SourceName.PUBLIC,
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

        async def sleep(delay: float) -> None:
            delays.append(delay)

        clock = _FakeClock()
        result = await SourceRetryExecutor().run(
            SourceCall(SourceName.PUBLIC, transport_failure),
            RetryPolicy(),
            sleep,
            clock,
            OperationDeadline.from_timeout_ms(100, clock),
        )

        self.assertEqual(1, calls)
        self.assertEqual([], delays)
        self.assertEqual(1, result.attempt.transport_attempts)

    async def test_second_source_uses_the_same_operation_deadline(
        self,
    ) -> None:
        clock = _FakeClock()
        deadline = OperationDeadline.from_timeout_ms(300, clock)
        first_calls = 0
        second_calls = 0
        delays: list[float] = []

        async def first_source() -> SourceResult[None]:
            nonlocal first_calls
            first_calls += 1
            clock.advance(0.3)
            return source_failure(
                SourceName.PUBLIC,
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

        async def second_source() -> SourceResult[None]:
            nonlocal second_calls
            second_calls += 1
            return source_failure(
                SourceName.BROWSER,
                SourceOutcome.TRANSPORT_ERROR,
                SafeErrorCode.TRANSPORT_FAILED,
            )

        async def sleep(delay: float) -> None:
            delays.append(delay)
            clock.advance(delay)

        executor = SourceRetryExecutor()
        await executor.run(
            SourceCall(SourceName.PUBLIC, first_source),
            RetryPolicy(),
            sleep,
            clock,
            deadline,
        )
        result = await executor.run(
            SourceCall(SourceName.BROWSER, second_source),
            RetryPolicy(),
            sleep,
            clock,
            deadline,
        )

        self.assertEqual(1, first_calls)
        self.assertEqual(0, second_calls)
        self.assertEqual([], delays)
        self.assertEqual(SourceOutcome.TRANSPORT_ERROR, result.outcome)
        self.assertEqual(1, result.attempt.transport_attempts)

    async def test_non_retriable_outcomes_are_called_once(self) -> None:
        cases = (
            (
                SourceOutcome.CHALLENGE,
                SafeErrorCode.CHALLENGE_DETECTED,
            ),
            (SourceOutcome.PARSE_DRIFT, SafeErrorCode.PARSE_DRIFT),
            (SourceOutcome.AUTH_ERROR, SafeErrorCode.AUTH_FAILED),
            (SourceOutcome.INVALID_CONFIG, SafeErrorCode.INVALID_CONFIG),
        )

        for outcome, error_code in cases:
            with self.subTest(outcome=outcome):
                calls = 0

                async def failure() -> SourceResult[None]:
                    nonlocal calls
                    calls += 1
                    return source_failure(
                        SourceName.PUBLIC,
                        outcome,
                        error_code,
                    )

                result = await SourceRetryExecutor().run(
                    SourceCall(SourceName.PUBLIC, failure),
                    RetryPolicy(),
                    _no_sleep,
                    lambda: 0.0,
                    _deadline(),
                )

                self.assertEqual(1, calls)
                self.assertEqual(1, result.attempt.transport_attempts)

    async def test_success_and_empty_are_called_once(self) -> None:
        cases = (
            (SourceOutcome.SUCCESS, source_success),
            (SourceOutcome.EMPTY, source_empty),
        )

        for outcome, factory in cases:
            with self.subTest(outcome=outcome):
                calls = 0

                async def terminal() -> SourceResult[object]:
                    nonlocal calls
                    calls += 1
                    if outcome is SourceOutcome.SUCCESS:
                        return factory(SourceName.PUBLIC, ('item',))
                    return factory(SourceName.PUBLIC)

                result = await SourceRetryExecutor().run(
                    SourceCall(SourceName.PUBLIC, terminal),
                    RetryPolicy(),
                    _no_sleep,
                    lambda: 0.0,
                    _deadline(),
                )

                self.assertEqual(1, calls)
                self.assertEqual(outcome, result.outcome)
                self.assertEqual(1, result.attempt.transport_attempts)

    def test_policy_rejects_retry_budgets_outside_one_or_two_attempts(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, 'between 1 and 2'):
            RetryPolicy(max_attempts=0)

        with self.assertRaisesRegex(ValueError, 'between 1 and 2'):
            RetryPolicy(max_attempts=3)


async def _no_sleep(delay: float) -> None:
    del delay


class _FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, elapsed: float) -> None:
        self._now += elapsed


def _deadline() -> OperationDeadline:
    return OperationDeadline.from_timeout_ms(1_000, lambda: 0.0)


if __name__ == '__main__':
    unittest.main()
