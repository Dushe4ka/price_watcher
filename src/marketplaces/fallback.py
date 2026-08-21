from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from src.marketplaces.contracts import (
    MarketplaceName,
    MarketplaceOperation,
    MarketplaceResult,
    SourceAttempt,
    SourceName,
    SourceOutcome,
    SourceResult,
)


T = TypeVar('T')


@dataclass(frozen=True, slots=True)
class SourceCall(Generic[T]):
    """One source invocation in an ordered marketplace fallback chain."""

    source: SourceName
    invoke: Callable[[], Awaitable[SourceResult[T]]]


async def execute_fallback(
    marketplace: MarketplaceName,
    operation: MarketplaceOperation,
    calls: Sequence[SourceCall[T]],
) -> MarketplaceResult[T]:
    """Run each distinct source once until a terminal source result arrives."""
    _validate_distinct_sources(calls)
    attempts: list[SourceAttempt] = []

    for call in calls:
        result = await call.invoke()
        if result.source is not call.source:
            raise ValueError('result source does not match source call')
        attempts.append(result.attempt)
        if result.outcome in _TERMINAL_OUTCOMES:
            return MarketplaceResult(
                marketplace=marketplace,
                operation=operation,
                outcome=result.outcome,
                value=result.value,
                attempts=tuple(attempts),
                selected_source=call.source,
            )

    if not attempts:
        raise ValueError('fallback requires at least one source call')

    return MarketplaceResult(
        marketplace=marketplace,
        operation=operation,
        outcome=attempts[-1].outcome,
        value=None,
        attempts=tuple(attempts),
        selected_source=None,
    )


_TERMINAL_OUTCOMES = frozenset(
    (
        SourceOutcome.SUCCESS,
        SourceOutcome.EMPTY,
        SourceOutcome.NOT_FOUND,
    )
)


def _validate_distinct_sources(calls: Sequence[SourceCall[object]]) -> None:
    sources = tuple(call.source for call in calls)
    if len(sources) != len(set(sources)):
        raise ValueError('fallback contains duplicate source calls')
