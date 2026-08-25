from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.marketplaces.contracts import (
        MarketplaceName,
        MarketplaceOperation,
        SourceOutcome,
        SourceAttempt,
    )


MAX_RETRY_AFTER_MS = 300_000


def bounded_retry_after_ms(value: str | None) -> int | None:
    """Parse a ``Retry-After`` header into a bounded millisecond hint.

    Anything that is not a plain non-negative integer count of seconds is
    discarded rather than echoed: the header is attacker-influenced text and
    must never reach a counter, a log line or an exception message.
    """
    if value is None:
        return None
    normalized = value.strip().lstrip('0') or '0'
    if not normalized.isdigit():
        return None
    if len(normalized) > len(str(MAX_RETRY_AFTER_MS)):
        return MAX_RETRY_AFTER_MS
    try:
        return min(int(normalized) * 1000, MAX_RETRY_AFTER_MS)
    except (OverflowError, ValueError):
        return None


class SafeErrorCode(StrEnum):
    AUTH_FAILED = 'auth_failed'
    CHALLENGE_DETECTED = 'challenge_detected'
    CHALLENGE_UNSUPPORTED = 'challenge_unsupported'
    CONTENT_TOO_LARGE = 'content_too_large'
    INVALID_CONFIG = 'invalid_config'
    PARSE_DRIFT = 'parse_drift'
    PROFILE_LOCKED = 'profile_locked'
    RATE_LIMITED = 'rate_limited'
    TIMEOUT = 'timeout'
    TRANSPORT_FAILED = 'transport_failed'


class MarketplaceOperationError(RuntimeError):
    """A marketplace operation failed without exposing raw source details."""

    def __init__(
        self,
        marketplace: MarketplaceName,
        operation: MarketplaceOperation,
        error_code: SafeErrorCode,
        attempts: tuple[SourceAttempt, ...],
        cause: Exception | None = None,
    ) -> None:
        self.marketplace = marketplace
        self.operation = operation
        self.error_code = error_code
        self.attempts = attempts
        self.__cause__ = cause
        super().__init__(
            f'marketplace operation failed: {marketplace} '
            f'{operation} ({error_code})'
        )


class MarketplaceSourceError(RuntimeError):
    """A typed source failure that never renders raw transport details."""

    def __init__(
        self,
        outcome: SourceOutcome,
        error_code: SafeErrorCode,
        cause: Exception | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        from src.marketplaces.contracts import SourceOutcome

        if outcome in (
            SourceOutcome.SUCCESS,
            SourceOutcome.EMPTY,
            SourceOutcome.NOT_FOUND,
        ):
            raise ValueError('source error requires a failure outcome')
        if retry_after_ms is not None:
            if retry_after_ms < 0:
                raise ValueError('retry_after_ms must not be negative')
            if outcome is not SourceOutcome.RATE_LIMITED:
                raise ValueError(
                    'retry_after_ms requires a rate limited outcome'
                )
        self.outcome = outcome
        self.error_code = error_code
        self.retry_after_ms = retry_after_ms
        self.__cause__ = cause
        super().__init__(f'marketplace source failed ({error_code})')
