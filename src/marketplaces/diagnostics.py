"""Fold source-level outcomes into existing pipeline run counters.

Only the safe fields of :class:`SourceAttempt` are surfaced: source, outcome
and safe error code. No query, product identity, URL or body ever reaches the
counters or the logs built from them.
"""

from __future__ import annotations

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
    'summarize_attempts',
)
