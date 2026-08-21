from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TypeVar

from src.marketplaces.contracts import (
    SourceName,
    SourceOutcome,
    SourceResult,
    SourceAttempt,
)
from src.marketplaces.errors import SafeErrorCode
from src.marketplaces.fallback import SourceCall


T = TypeVar('T')
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """A bounded, source-local retry budget for transient failures only."""

    max_attempts: int = 2
    base_delay_ms: int = 250
    max_delay_ms: int = 1000

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 2:
            raise ValueError('max_attempts must be between 1 and 2')
        if self.base_delay_ms < 0:
            raise ValueError('base_delay_ms must not be negative')
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError(
                'max_delay_ms must not be less than base_delay_ms'
            )


@dataclass(frozen=True, slots=True)
class OperationDeadline:
    """Absolute deadline shared by all source calls in one operation."""

    expires_at: float

    @classmethod
    def from_timeout_ms(
        cls,
        timeout_ms: int,
        clock: Clock,
    ) -> OperationDeadline:
        """Create one immutable deadline from an operation timeout."""
        if timeout_ms < 0:
            raise ValueError('timeout_ms must not be negative')
        return cls(expires_at=clock() + timeout_ms / 1000)


class SourceRetryExecutor:
    """The sole owner of internal retries for an individual source call."""

    async def run(
        self,
        call: SourceCall[T],
        policy: RetryPolicy,
        sleep: Sleep,
        clock: Clock,
        deadline: OperationDeadline,
    ) -> SourceResult[T]:
        """Invoke a source at most ``policy.max_attempts`` times."""
        if clock() >= deadline.expires_at:
            return _deadline_expired_result(call.source)

        for transport_attempt in range(1, policy.max_attempts + 1):
            result = await call.invoke()
            if result.source is not call.source:
                raise ValueError('result source does not match source call')
            if not _can_retry(result.outcome, transport_attempt, policy):
                return _with_transport_attempts(result, transport_attempt)
            delay = min(policy.base_delay_ms, policy.max_delay_ms) / 1000
            if clock() + delay > deadline.expires_at:
                return _with_transport_attempts(result, transport_attempt)
            await sleep(delay)

        raise RuntimeError('retry loop exited without a source result')


def _can_retry(
    outcome: SourceOutcome,
    transport_attempt: int,
    policy: RetryPolicy,
) -> bool:
    return (
        outcome in _RETRIABLE_OUTCOMES
        and transport_attempt < policy.max_attempts
    )


def _deadline_expired_result(source: SourceName) -> SourceResult[None]:
    return SourceResult(
        source=source,
        outcome=SourceOutcome.TRANSPORT_ERROR,
        value=None,
        attempt=SourceAttempt(
            source=source,
            outcome=SourceOutcome.TRANSPORT_ERROR,
            duration_ms=0,
            item_count=0,
            error_code=SafeErrorCode.TRANSPORT_FAILED,
            transport_attempts=0,
        ),
    )


def _with_transport_attempts(
    result: SourceResult[T],
    transport_attempts: int,
) -> SourceResult[T]:
    return replace(
        result,
        attempt=replace(
            result.attempt,
            transport_attempts=transport_attempts,
        ),
    )


_RETRIABLE_OUTCOMES = frozenset(
    (SourceOutcome.RATE_LIMITED, SourceOutcome.TRANSPORT_ERROR)
)
