"""A single bounded live probe of one marketplace operation, for operators.

The probe is inert by default. Nothing is imported lazily to hide it and
nothing is composed eagerly to leak it: the gate is checked before any
service is built, so an ungated run cannot reach a marketplace at all. It is
never run by CI and never run by the application.

Output is restricted to :mod:`src.marketplaces.telemetry`'s allowlist, so a
probe transcript can be pasted into an issue without carrying a query, a
product identity, a URL, a cookie, a token or a response body.

Run (only when you mean it)::

    LIVE_MARKETPLACE_TESTS=1 python -m scripts.live_marketplace_probe \\
        --marketplace ozon --operation crawl_category
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# An operator reaches for ``python scripts/live_marketplace_probe.py`` as
# readily as for ``python -m``; the repository root has to be importable
# either way, or the gate message never gets a chance to print.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.marketplaces.contracts import (
    CategoryRequest,
    MarketplaceName,
    MarketplaceOperation,
    ProductRequest,
    SearchRequest,
    SourceOutcome,
)
from src.marketplaces.telemetry import (
    format_safe_fields,
    safe_attempt_fields,
    safe_attempt_rows,
    safe_exception_label,
    silence_transport_request_logs,
)


LIVE_GATE_ENV = 'LIVE_MARKETPLACE_TESTS'
LIVE_GATE_VALUE = '1'

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DISABLED = 2
EXIT_USAGE = 3

#: One page, a handful of cards: a probe must never behave like a crawl.
PROBE_PAGE = 1
PROBE_LIMIT = 3
PROBE_QUERY = 'наушники'

MARKETPLACES: tuple[MarketplaceName, ...] = (
    'wildberries',
    'ozon',
    'yandex_market',
)

_SUCCESSFUL_OUTCOMES = frozenset(
    (SourceOutcome.SUCCESS, SourceOutcome.EMPTY, SourceOutcome.NOT_FOUND)
)

Writer = Callable[[str], None]
ServiceFactory = Callable[[str], Any]

#: ``None`` means "compose the real service, lazily". The composition root
#: pulls in application settings, so it must stay behind the gate: an ungated
#: run has to be able to print its refusal on a machine with no configuration.
DEFAULT_SERVICE_FACTORY: ServiceFactory | None = None


class ProbeInputError(ValueError):
    """A rejected probe argument, carrying only a fixed, curated message.

    Every message raised as this type is a literal composed from the closed
    option sets, so it can be printed verbatim. Any *other* ``ValueError``
    reaching a handler — a pydantic ``ValidationError`` from loading settings,
    for instance, which renders ``input_value=`` — is rendered by class name
    only. See :func:`_render_input_rejection`.
    """


class LiveProbeDisabled(RuntimeError):
    """The live gate is not explicitly enabled, so nothing may be probed."""

    def __init__(self) -> None:
        super().__init__(
            f'live marketplace probes are disabled: set {LIVE_GATE_ENV}='
            f'{LIVE_GATE_VALUE} to run one on purpose'
        )


def assert_live_tests_enabled(env: Mapping[str, str]) -> None:
    """Raise unless the operator opted in with the exact gate value."""
    if env.get(LIVE_GATE_ENV) != LIVE_GATE_VALUE:
        raise LiveProbeDisabled()


def parse_marketplace(value: str) -> MarketplaceName:
    """Resolve one marketplace name, never echoing the rejected input."""
    normalized = value.strip().lower()
    for marketplace in MARKETPLACES:
        if marketplace == normalized:
            return marketplace
    raise ProbeInputError(
        'unsupported marketplace; expected one of: '
        + ', '.join(MARKETPLACES)
    )


def parse_operation(value: str) -> MarketplaceOperation:
    """Resolve one operation name, never echoing the rejected input."""
    normalized = value.strip().lower()
    for operation in MarketplaceOperation:
        if operation.value == normalized:
            return operation
    raise ProbeInputError(
        'unsupported operation; expected one of: '
        + ', '.join(item.value for item in MarketplaceOperation)
    )


async def run_one_probe(
    marketplace: MarketplaceName,
    operation: MarketplaceOperation,
    *,
    category_slug: str | None = None,
    query: str | None = None,
    product_id: str | None = None,
    service_factory: ServiceFactory | None = DEFAULT_SERVICE_FACTORY,
    writer: Writer = print,
) -> int:
    """Run exactly one bounded operation and report only safe aggregates."""
    silence_transport_request_logs()
    if service_factory is None:
        # The gate guards the composition point itself, not just ``main``:
        # importing ``run_one_probe`` from elsewhere must not be a way around
        # it. An injected factory is a fake by definition and stays ungated.
        #
        # This must run *before* ``_build_request``: defaulting a category
        # slug reads the categories configuration, which imports application
        # settings. An ungated run may not load marketplace configuration at
        # all, so nothing that can touch it may precede the gate.
        try:
            assert_live_tests_enabled(os.environ)
        except LiveProbeDisabled as exc:
            writer(str(exc))
            return EXIT_DISABLED
        from src.marketplaces.service import get_marketplace_service

        service_factory = get_marketplace_service

    try:
        request = _build_request(
            operation,
            marketplace,
            category_slug=category_slug,
            query=query,
            product_id=product_id,
        )
    except ValueError as exc:
        writer(_render_input_rejection(exc))
        return EXIT_USAGE

    service = service_factory(marketplace)
    try:
        result = await _invoke(service, operation, request)
    except Exception as exc:  # noqa: BLE001 - operators need an exit code
        writer(
            f'probe raised {safe_exception_label(exc)} '
            f'on {marketplace} {operation.value}'
        )
        return EXIT_FAILED

    writer('result ' + format_safe_fields(safe_attempt_fields(result)))
    for row in safe_attempt_rows(result):
        writer('attempt ' + format_safe_fields(row))

    outcome = getattr(result, 'outcome', None)
    return EXIT_OK if outcome in _SUCCESSFUL_OUTCOMES else EXIT_FAILED


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    service_factory: ServiceFactory | None = DEFAULT_SERVICE_FACTORY,
    writer: Writer = print,
) -> int:
    """Parse arguments, enforce the gate, then run at most one probe."""
    environment = os.environ if env is None else env
    args = _build_parser().parse_args(argv)

    try:
        assert_live_tests_enabled(environment)
    except LiveProbeDisabled as exc:
        writer(str(exc))
        return EXIT_DISABLED

    try:
        marketplace = parse_marketplace(args.marketplace)
        operation = parse_operation(args.operation)
    except ValueError as exc:
        writer(_render_input_rejection(exc))
        return EXIT_USAGE

    return asyncio.run(
        run_one_probe(
            marketplace,
            operation,
            category_slug=args.category,
            query=args.query,
            product_id=args.product_id,
            service_factory=service_factory,
            writer=writer,
        ),
    )


def _render_input_rejection(exc: ValueError) -> str:
    """Describe a rejected input without ever echoing an exception message.

    Only :class:`ProbeInputError` carries a message this module wrote, so only
    its message may be printed. Every other ``ValueError`` — most importantly a
    pydantic ``ValidationError`` raised while importing application settings,
    which renders the offending ``input_value=`` — is reduced to its class
    name by :func:`~src.marketplaces.telemetry.safe_exception_label`.
    """
    if isinstance(exc, ProbeInputError):
        return f'probe input rejected: {exc}'
    return f'probe input rejected ({safe_exception_label(exc)})'


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='live_marketplace_probe',
        description='Run one bounded live marketplace probe.',
    )
    parser.add_argument('--marketplace', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument(
        '--category',
        default=None,
        help='category slug for crawl_category; defaults to configuration',
    )
    parser.add_argument(
        '--query',
        default=None,
        help='search term for search_products; never printed back',
    )
    parser.add_argument(
        '--product-id',
        default=None,
        help='product id for parse_product; never printed back',
    )
    return parser


def _build_request(
    operation: MarketplaceOperation,
    marketplace: MarketplaceName,
    *,
    category_slug: str | None,
    query: str | None,
    product_id: str | None,
) -> CategoryRequest | ProductRequest | SearchRequest:
    if operation is MarketplaceOperation.CRAWL_CATEGORY:
        slug = category_slug or _first_configured_slug(marketplace)
        if not slug:
            raise ProbeInputError('crawl_category needs --category')
        return CategoryRequest(category_slug=slug, limit=PROBE_LIMIT)
    if operation is MarketplaceOperation.SEARCH_PRODUCTS:
        return SearchRequest(
            query=query or PROBE_QUERY,
            limit=PROBE_LIMIT,
            page=PROBE_PAGE,
        )
    if not product_id:
        raise ProbeInputError('parse_product needs --product-id')
    return ProductRequest(product_id)


def _first_configured_slug(marketplace: MarketplaceName) -> str | None:
    from src.services.categories_loader import load_categories_config

    try:
        config = load_categories_config()
    except Exception:  # noqa: BLE001 - a missing config is not a probe result
        return None
    for category in config.categories:
        for entry in category.marketplaces:
            if entry.marketplace == marketplace:
                return category.slug
    return None


async def _invoke(
    service: Any,
    operation: MarketplaceOperation,
    request: Any,
) -> Any:
    if operation is MarketplaceOperation.CRAWL_CATEGORY:
        return await service.crawl_category(request)
    if operation is MarketplaceOperation.SEARCH_PRODUCTS:
        return await service.search_products(request)
    return await service.parse_product(request)


__all__ = (
    'DEFAULT_SERVICE_FACTORY',
    'EXIT_DISABLED',
    'EXIT_FAILED',
    'EXIT_OK',
    'EXIT_USAGE',
    'LIVE_GATE_ENV',
    'LIVE_GATE_VALUE',
    'LiveProbeDisabled',
    'PROBE_LIMIT',
    'PROBE_PAGE',
    'ProbeInputError',
    'assert_live_tests_enabled',
    'main',
    'parse_marketplace',
    'parse_operation',
    'run_one_probe',
)


if __name__ == '__main__':
    sys.exit(main())
