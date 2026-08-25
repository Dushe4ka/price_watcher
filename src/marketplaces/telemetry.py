"""An exact allowlist for what a marketplace attempt may ever disclose.

Nothing here inspects a payload, a request or a transport object. Every
rendered value is re-derived from a closed enum or coerced to an integer, so
a query, a product identity, a URL, a cookie, an ``Authorization`` header, a
proxy, a CAPTCHA token or a raw response body cannot reach a log line even if
a caller hands this module an object that carries one.
"""

from __future__ import annotations

import logging
from typing import Any

from src.marketplaces.contracts import (
    MarketplaceOperation,
    SourceName,
    SourceOutcome,
)
from src.marketplaces.errors import SafeErrorCode


SAFE_ATTEMPT_FIELDS: tuple[str, ...] = (
    'marketplace',
    'operation',
    'source',
    'outcome',
    'duration_ms',
    'item_count',
    'transport_attempts',
    'error_code',
    'retry_after_ms',
)

UNKNOWN = 'unknown'

_MARKETPLACES = frozenset({'wildberries', 'ozon', 'yandex_market'})

#: Third-party loggers that render whole request URLs at ``INFO``.
_NOISY_TRANSPORT_LOGGERS: tuple[str, ...] = (
    'httpcore',
    'httpx',
    'telegram.request',
    'urllib3',
)


def safe_attempt_fields(
    result: Any,
    attempt: Any | None = None,
) -> dict[str, object]:
    """Render exactly the allowlisted diagnostics for one marketplace result.

    ``attempt`` defaults to the attempt the result actually selected, falling
    back to the last attempt made, so a single line describes the outcome the
    caller observed rather than an intermediate one.
    """
    chosen = attempt if attempt is not None else _selected_attempt(result)
    return {
        'marketplace': _safe_marketplace(getattr(result, 'marketplace', None)),
        'operation': _safe_operation(getattr(result, 'operation', None)),
        'source': _safe_source(getattr(chosen, 'source', None)),
        'outcome': _safe_outcome(
            getattr(chosen, 'outcome', None),
            getattr(result, 'outcome', None),
        ),
        'duration_ms': _safe_count(getattr(chosen, 'duration_ms', None)),
        'item_count': _safe_count(getattr(chosen, 'item_count', None)),
        'transport_attempts': _safe_count(
            getattr(chosen, 'transport_attempts', None),
        ),
        'error_code': _safe_error_code(getattr(chosen, 'error_code', None)),
        'retry_after_ms': _safe_optional_count(
            getattr(chosen, 'retry_after_ms', None),
        ),
    }


def safe_attempt_rows(result: Any) -> tuple[dict[str, object], ...]:
    """Render one allowlisted row per attempt of a marketplace result."""
    attempts = getattr(result, 'attempts', ()) or ()
    try:
        ordered = tuple(attempts)
    except TypeError:
        return ()
    return tuple(
        safe_attempt_fields(result, attempt) for attempt in ordered
    )


def format_safe_fields(fields: dict[str, object]) -> str:
    """Render allowlisted fields as one single-line ``key=value`` string."""
    return ' '.join(
        f'{name}={_render(fields.get(name))}' for name in SAFE_ATTEMPT_FIELDS
    )


def safe_exception_label(exc: object) -> str:
    """Return an exception's type name, never its message.

    An exception raised by a transport, a Telegram client or a parser routinely
    quotes the URL, the body or the header that produced it, so only the class
    name is safe to log.
    """
    return type(exc).__name__


def silence_transport_request_logs() -> None:
    """Stop third-party clients from logging request lines with URLs.

    ``httpx`` logs ``HTTP Request: GET <url> "<status>"`` at ``INFO``, which
    puts a search query and a product URL into the process log the moment a
    composition root calls :func:`logging.basicConfig`. Raising these loggers
    to ``WARNING`` keeps their failures visible while dropping the request
    lines the project is not allowed to emit.
    """
    for name in _NOISY_TRANSPORT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _selected_attempt(result: Any) -> Any | None:
    attempts = getattr(result, 'attempts', ()) or ()
    try:
        ordered = tuple(attempts)
    except TypeError:
        return None
    if not ordered:
        return None
    selected = getattr(result, 'selected_source', None)
    if isinstance(selected, SourceName):
        for attempt in ordered:
            if getattr(attempt, 'source', None) is selected:
                return attempt
    return ordered[-1]


def _safe_marketplace(value: object) -> str:
    if isinstance(value, str) and value in _MARKETPLACES:
        return value
    return UNKNOWN


def _safe_operation(value: object) -> str:
    if isinstance(value, MarketplaceOperation):
        return value.value
    return UNKNOWN


def _safe_source(value: object) -> str | None:
    if isinstance(value, SourceName):
        return value.value
    return None


def _safe_outcome(value: object, fallback: object) -> str:
    for candidate in (value, fallback):
        if isinstance(candidate, SourceOutcome):
            return candidate.value
    return UNKNOWN


def _safe_error_code(value: object) -> str | None:
    if isinstance(value, SafeErrorCode):
        return value.value
    return None


def _safe_count(value: object) -> int:
    count = _safe_optional_count(value)
    return 0 if count is None else count


def _safe_optional_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _render(value: object) -> str:
    if value is None:
        return 'none'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return UNKNOWN


__all__ = (
    'SAFE_ATTEMPT_FIELDS',
    'UNKNOWN',
    'format_safe_fields',
    'safe_attempt_fields',
    'safe_attempt_rows',
    'safe_exception_label',
    'silence_transport_request_logs',
)
