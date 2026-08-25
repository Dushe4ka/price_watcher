#!/usr/bin/env python3
"""Smoke the marketplace stack in mock mode, never touching a marketplace.

Two modes:

``--mode controlled`` (default)
    Boots the real marketplace runtime, the real API application and the
    real bot shutdown hook in this process, under a mock-mode environment,
    and checks that every marketplace operation answers ``disabled`` — that
    is the machine-checkable form of "no live marketplace traffic".

``--mode compose``
    Brings the Compose stack up with the same mock-mode environment,
    polls the API, checks the bot container is running and then brings the
    stack down again, asserting a clean shutdown. Requires a working Docker
    daemon and exits with code 2, loudly, when there is none.

What "mock mode" means with today's settings
--------------------------------------------
The original spec mentioned ``CAPTCHA_GATEWAY_MODE=mock`` and
``MARKETPLACES=mock``. Neither exists in ``Settings`` as it was actually
built. The equivalent, expressible today, is:

* every source chain reduced to ``apify``, the only source that refuses to
  emit a request until it is explicitly configured;
* no Apify token and no actor IDs, so every operation short-circuits to
  ``disabled`` before any socket is opened;
* ``CAPTCHA_ADAPTER_MODE`` and ``SMARTCAPTCHA_MODE`` left ``disabled``, so
  no CAPTCHA vendor is contacted either.

Nothing here starts a browser: with no browser in any chain the registry
never builds a session manager.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNAVAILABLE = 2

MOCK_ENVIRONMENT: dict[str, str] = {
    'WILDBERRIES_SOURCE_CHAIN': 'apify',
    'OZON_SOURCE_CHAIN': 'apify',
    'YANDEX_MARKET_SOURCE_CHAIN': 'apify',
    'APIFY_TOKEN': '',
    'CAPTCHA_ADAPTER_MODE': 'disabled',
    'SMARTCAPTCHA_MODE': 'disabled',
    'WEB_CONCURRENCY': '1',
    'DEALS_ENABLED': 'false',
}

_REQUIRED_SETTINGS: dict[str, str] = {
    'DB_DIALECT': 'postgresql',
    'DB_DRIVER': 'asyncpg',
    'SECRET': 'smoke-secret',
    'FIRST_SUPERUSER_EMAIL': 'smoke@example.invalid',
    'FIRST_SUPERUSER_PASSWORD': 'smoke-password',
    'POSTGRES_USER': 'smoke-user',
    'POSTGRES_PASSWORD': 'smoke-password',
    'POSTGRES_DB': 'smoke-db',
    'POSTGRES_HOST': 'db',
    'POSTGRES_PORT': '5432',
}

_COMPOSE_FILES = ('docker-compose.yml', 'docker-compose.local.yml')
_COMPOSE_SERVICES = ('db', 'api', 'bot')
_COMPOSE_PROJECT = 'price-watcher-smoke'
_API_HEALTH_URL = 'http://127.0.0.1:8000/openapi.json'

# docker-compose.yml pins these names, so a project prefix cannot keep the
# smoke stack away from a container of the same name already on the host.
# Taking one over would stop somebody else's service, and ``down -v`` would
# then delete its volumes, so a collision aborts the run instead.
_PINNED_CONTAINER_NAMES = ('api', 'telegram_bot', 'db', 'nginx')


class SmokeFailure(RuntimeError):
    """A smoke check produced an unacceptable result."""


class SmokeUnavailable(RuntimeError):
    """The environment cannot run this mode at all."""


def main(argv: list[str] | None = None) -> int:
    """Run the requested smoke mode and report a single verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--mode',
        choices=('controlled', 'compose'),
        default='controlled',
        help='controlled runs in-process; compose needs a Docker daemon',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=180.0,
        help='seconds to wait for compose services to answer',
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.mode == 'controlled':
            _run_controlled()
        else:
            _run_compose(arguments.timeout)
    except SmokeUnavailable as exc:
        print(f'SKIPPED: {exc}')
        return EXIT_UNAVAILABLE
    except SmokeFailure as exc:
        print(f'FAILED: {exc}')
        return EXIT_FAILED
    print(f'OK: marketplace stack smoke passed in {arguments.mode} mode')
    return EXIT_OK


def _run_controlled() -> None:
    _apply_mock_environment()
    settings = _mock_settings()
    _check_no_live_source_is_configured(settings)
    outcomes = asyncio.run(_run_runtime_lifecycle(settings))
    _check_every_outcome_is_disabled(outcomes)
    _check_api_serves()
    _check_bot_shutdown_is_graceful()


def _apply_mock_environment() -> None:
    for name, value in _REQUIRED_SETTINGS.items():
        os.environ.setdefault(name, value)
    os.environ.update(MOCK_ENVIRONMENT)


def _mock_settings() -> Any:
    from src.core.config import Settings

    values = {
        name.lower(): os.environ[name]
        for name in (*_REQUIRED_SETTINGS, *MOCK_ENVIRONMENT)
        if name in os.environ and name != 'WEB_CONCURRENCY'
    }
    return Settings(_env_file=None, **values)


def _check_no_live_source_is_configured(settings: Any) -> None:
    from src.marketplaces.contracts import SourceName
    from src.marketplaces.registry import MARKETPLACES

    for marketplace in MARKETPLACES:
        chain = settings.source_chain(marketplace)
        if chain != (SourceName.APIFY,):
            raise SmokeFailure(
                f'{marketplace} chain is not mock mode: {chain}',
            )
    if settings.apify_token.get_secret_value():
        raise SmokeFailure('mock mode must not carry an Apify token')
    if settings.captcha_adapter_mode != 'disabled':
        raise SmokeFailure('mock mode must not enable a CAPTCHA adapter')
    if settings.smartcaptcha_mode != 'disabled':
        raise SmokeFailure('mock mode must not enable SmartCaptcha')


async def _run_runtime_lifecycle(settings: Any) -> dict[str, str]:
    from src.marketplaces.contracts import ProductRequest
    from src.marketplaces.registry import (
        MARKETPLACES,
        MarketplaceSourceRegistry,
    )
    from src.marketplaces.service import (
        close_marketplace_services,
        configure_marketplace_registry,
        configure_marketplace_runtime,
        get_marketplace_service,
        start_marketplace_services,
    )

    registry = MarketplaceSourceRegistry(
        settings=settings,
        manager_factory=_forbidden_manager_factory,
        category_urls={marketplace: {} for marketplace in MARKETPLACES},
    )
    configure_marketplace_runtime('api')
    configure_marketplace_registry(registry)
    outcomes: dict[str, str] = {}
    try:
        await start_marketplace_services()
        for marketplace in MARKETPLACES:
            service = get_marketplace_service(marketplace)
            result = await service.parse_product(
                ProductRequest(product_id='1017'),
            )
            outcomes[marketplace] = str(result.outcome)
    finally:
        await close_marketplace_services()
        # Shutdown must stay safe when the supervisor calls it twice.
        await close_marketplace_services()
        configure_marketplace_registry(None)
    return outcomes


def _forbidden_manager_factory() -> Any:
    raise SmokeFailure(
        'mock mode must never compose a browser session manager',
    )


def _check_every_outcome_is_disabled(outcomes: dict[str, str]) -> None:
    if not outcomes:
        raise SmokeFailure('no marketplace operation ran')
    unexpected = {
        marketplace: outcome
        for marketplace, outcome in outcomes.items()
        if outcome != 'disabled'
    }
    if unexpected:
        raise SmokeFailure(
            f'mock mode produced non-disabled outcomes: {unexpected}',
        )
    print(f'  marketplace runtime: {json.dumps(outcomes, sort_keys=True)}')


def _check_api_serves() -> None:
    from fastapi.testclient import TestClient

    from src.main import app

    # No lifespan here on purpose: mock mode has no database, and the
    # marketplace half of the lifespan is exercised above instead.
    client = TestClient(app)
    response = client.get('/openapi.json')
    if response.status_code != 200:
        raise SmokeFailure(
            f'API did not serve its schema: HTTP {response.status_code}',
        )
    print('  api: /openapi.json answered 200')


def _check_bot_shutdown_is_graceful() -> None:
    from bot.deals_scheduler import bot_post_shutdown

    class _StoppedScheduler:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def shutdown(self, wait: bool = False) -> None:
            del wait
            self.shutdown_calls += 1

    class _Application:
        def __init__(self) -> None:
            self.bot_data: dict[str, object] = {
                'deals_scheduler': _StoppedScheduler(),
            }

    application = _Application()
    scheduler = application.bot_data['deals_scheduler']
    asyncio.run(bot_post_shutdown(application))
    if getattr(scheduler, 'shutdown_calls', 0) != 1:
        raise SmokeFailure('bot shutdown did not stop the deals scheduler')
    if 'deals_scheduler' in application.bot_data:
        raise SmokeFailure('bot shutdown left the scheduler attached')
    print('  bot: post-shutdown released the scheduler and services')


def _run_compose(timeout: float) -> None:
    _require_docker()
    _require_free_container_names()
    with tempfile.TemporaryDirectory(prefix='smoke-stack-') as directory:
        env_file = Path(directory) / 'mock.env'
        env_file.write_text(_render_env_file(), encoding='utf-8')
        base = _compose_command(env_file)
        _run(base + ['up', '-d', *_COMPOSE_SERVICES], timeout=timeout)
        try:
            _wait_for_api(timeout)
            _check_bot_container(base)
        finally:
            _run(
                base + ['down', '-v', '--remove-orphans'],
                timeout=timeout,
            )
    print('  compose: stack started, answered and stopped cleanly')


def _require_docker() -> None:
    try:
        completed = subprocess.run(
            ['docker', 'info'],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        raise SmokeUnavailable('docker is not installed') from None
    except subprocess.TimeoutExpired:
        raise SmokeUnavailable('docker did not respond') from None
    if completed.returncode != 0:
        raise SmokeUnavailable('the Docker daemon is not running')


def _require_free_container_names() -> None:
    completed = subprocess.run(
        ['docker', 'ps', '-a', '--format', '{{.Names}}'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeUnavailable('docker could not list containers')
    existing = set(completed.stdout.split())
    taken = sorted(existing.intersection(_PINNED_CONTAINER_NAMES))
    if taken:
        raise SmokeUnavailable(
            'these container names are already taken on this host and the '
            f'compose files pin them: {taken}',
        )


def _compose_command(env_file: Path) -> list[str]:
    command = [
        'docker',
        'compose',
        '-p',
        _COMPOSE_PROJECT,
        '--env-file',
        str(env_file),
    ]
    for name in _COMPOSE_FILES:
        command += ['-f', str(REPO_ROOT / name)]
    return command


def _render_env_file() -> str:
    values = {**_REQUIRED_SETTINGS, **MOCK_ENVIRONMENT}
    return '\n'.join(f'{name}={value}' for name, value in values.items())


def _run(command: list[str], *, timeout: float) -> None:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeFailure(
            f'{" ".join(command[:3])} failed: '
            f'{completed.stderr.strip()[:400]}',
        )


def _wait_for_api(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = 'no attempt was made'
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
                _API_HEALTH_URL,
                timeout=5,
            ) as response:
                if response.status == 200:
                    print('  compose api: /openapi.json answered 200')
                    return
                last_error = f'HTTP {response.status}'
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(2)
    raise SmokeFailure(f'API never became healthy: {last_error}')


def _check_bot_container(base: list[str]) -> None:
    completed = subprocess.run(
        base + ['ps', '--status', 'running', '--services'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    running = set(completed.stdout.split())
    if 'bot' not in running:
        raise SmokeFailure(f'bot service is not running: {sorted(running)}')
    print('  compose bot: container is running')


if __name__ == '__main__':
    sys.exit(main())
