"""Gated smoke check: one bounded Yandex Market crawl, safe output only.

This script performs no network call unless ``LIVE_MARKETPLACE_TESTS=1`` is
set, and prints only the allowlisted marketplace telemetry: never a product
identity, title, URL or response body.

Run::

    LIVE_MARKETPLACE_TESTS=1 python -m scripts.smoke_yandex_market_crawl
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

# Importable whether the operator reaches for ``-m`` or a bare script path.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.live_marketplace_probe import (
    DEFAULT_SERVICE_FACTORY,
    ServiceFactory,
    Writer,
    main as probe_main,
)


MARKETPLACE = 'yandex_market'
OPERATION = 'crawl_category'


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    service_factory: ServiceFactory | None = DEFAULT_SERVICE_FACTORY,
    writer: Writer = print,
) -> int:
    """Delegate to the gated probe runner with this marketplace pinned."""
    return probe_main(
        [
            '--marketplace',
            MARKETPLACE,
            '--operation',
            OPERATION,
            *(argv or []),
        ],
        env,
        service_factory=service_factory,
        writer=writer,
    )


if __name__ == '__main__':
    sys.exit(main())
