"""Fold source-level outcomes into existing pipeline run counters.

Only the safe fields of :class:`SourceAttempt` are surfaced: source, outcome
and safe error code. No query, product identity, URL or body ever reaches the
counters or the logs built from them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from src.marketplaces.contracts import (
    MarketplaceResult,
    SourceAttempt,
    SourceOutcome,
)


class MarketplaceRunStats(Protocol):
    """The counter surface a run report exposes for source diagnostics."""

    errors: int
    challenges: int
    fallback_activations: int
    source_outcomes: dict[str, dict[str, int]]

    def mp(self, marketplace: str) -> Any:
        """Return the per-marketplace counters for one marketplace."""


_SUCCESSFUL_OUTCOMES = frozenset(
    (
        SourceOutcome.SUCCESS,
        SourceOutcome.EMPTY,
    )
)


def accumulate_marketplace_diagnostics(
    stats: MarketplaceRunStats,
    result: MarketplaceResult[Any],
) -> None:
    """Record one marketplace result without miscounting a valid empty."""
    for attempt in result.attempts:
        _record_attempt(stats, attempt)
    if len(result.attempts) > 1:
        stats.fallback_activations += 1
    if result.outcome in _SUCCESSFUL_OUTCOMES:
        return
    stats.errors += 1
    marketplace_stats = stats.mp(result.marketplace)
    marketplace_stats.errors += 1


def accumulate_source_attempts(
    stats: MarketplaceRunStats,
    attempts: Sequence[SourceAttempt],
) -> None:
    """Record loose attempts that no single result speaks for.

    A market comparison check aggregates attempts across several
    marketplaces and several calls, so neither a result outcome nor a
    marketplace can be attributed to the sequence: only per-source outcome
    counters and challenges are folded in. Errors and fallback activations
    stay with :func:`accumulate_marketplace_diagnostics`, which sees whole
    results.
    """
    for attempt in attempts:
        _record_attempt(stats, attempt)


def summarize_attempts(result: MarketplaceResult[Any]) -> str:
    """Render one safe single-line summary of a marketplace result."""
    trail = ' '.join(
        f'{attempt.source.value}={attempt.outcome.value}'
        for attempt in result.attempts
    )
    return f'{result.marketplace} {result.operation.value} [{trail}]'


def _record_attempt(
    stats: MarketplaceRunStats,
    attempt: SourceAttempt,
) -> None:
    outcomes = stats.source_outcomes.setdefault(attempt.source.value, {})
    outcome = attempt.outcome.value
    outcomes[outcome] = outcomes.get(outcome, 0) + 1
    if attempt.outcome is SourceOutcome.CHALLENGE:
        stats.challenges += 1


__all__ = (
    'MarketplaceRunStats',
    'accumulate_marketplace_diagnostics',
    'accumulate_source_attempts',
    'summarize_attempts',
)
