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
    ) -> None:
        from src.marketplaces.contracts import SourceOutcome

        if outcome in (
            SourceOutcome.SUCCESS,
            SourceOutcome.EMPTY,
            SourceOutcome.NOT_FOUND,
        ):
            raise ValueError('source error requires a failure outcome')
        self.outcome = outcome
        self.error_code = error_code
        self.__cause__ = cause
        super().__init__(f'marketplace source failed ({error_code})')
