from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.marketplaces.contracts import (
        MarketplaceName,
        MarketplaceOperation,
        SourceAttempt,
    )


class SafeErrorCode(StrEnum):
    AUTH_FAILED = 'auth_failed'
    CHALLENGE_DETECTED = 'challenge_detected'
    CHALLENGE_UNSUPPORTED = 'challenge_unsupported'
    CONTENT_TOO_LARGE = 'content_too_large'
    INVALID_CONFIG = 'invalid_config'
    PARSE_DRIFT = 'parse_drift'
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
