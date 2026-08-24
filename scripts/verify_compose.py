#!/usr/bin/env python3
"""Verify the rendered Compose policy for persistent browser fallbacks.

Run from the repository root:
    python scripts/verify_compose.py docker-compose.yml \\
        docker-compose.production.yml

The files are merged in the order Compose itself would merge them and the
result is checked against the deployment policy this project depends on:
each browser-owning process keeps a private persistent profile, runs as a
single non-root worker, and never publishes a browser-control endpoint.

The merge is done in-process rather than through ``docker compose config``
so the same check runs in CI and in the unit suite, where no Docker daemon
exists, and so short-form volume strings survive verbatim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

BASE_COMPOSE_FILE = 'docker-compose.yml'
LOCAL_COMPOSE_FILE = 'docker-compose.local.yml'
PRODUCTION_COMPOSE_FILE = 'docker-compose.production.yml'
SECCOMP_PROFILE_FILE = 'infra/playwright/seccomp_profile.json'
CI_WORKFLOW_FILE = '.github/workflows/ci.yml'

BROWSER_PROFILE_TARGET = '/data/browser-profiles'
BROWSER_SERVICES = ('api', 'bot')
PRIVATE_SERVICES = ('api', 'bot', 'db')
PROFILE_VOLUMES = {
    'api': 'api_browser_profiles',
    'bot': 'bot_browser_profiles',
}
RUNTIME_ROLES = {'api': 'api', 'bot': 'bot'}
LEGACY_PROFILE_VOLUME = 'ozon_profile'
BROWSER_CONTROL_PORTS = ('9222', '9229', '4444', '5900', '6080')

SHARED_STAGE_BEGIN = '# >>> shared browser runtime >>>'
SHARED_STAGE_END = '# <<< shared browser runtime <<<'
BROWSER_IMAGES = ('Dockerfile.api', 'Dockerfile.bot')

MAIN_REQUIREMENTS_FILE = 'requirements.txt'
VENDOR_REQUIREMENTS_FILE = 'vendor/ohmycaptcha/requirements.txt'


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(document, dict):
        raise ValueError(f'{path} is not a Compose mapping')
    return _normalize(document)


def _normalize(document: dict[str, Any]) -> dict[str, Any]:
    """Put environment blocks in mapping form so overlays merge per key."""
    services = document.get('services')
    if not isinstance(services, dict):
        return document
    for service in services.values():
        if not isinstance(service, dict):
            continue
        environment = service.get('environment')
        if isinstance(environment, list):
            service['environment'] = dict(
                _split_environment_entry(entry) for entry in environment
            )
    return document


def _split_environment_entry(entry: str) -> tuple[str, str]:
    name, separator, value = str(entry).partition('=')
    return name, value if separator else ''


def volume_target(entry: Any) -> str:
    """Return the container path a service volume entry mounts onto."""
    if isinstance(entry, Mapping):
        return str(entry.get('target', ''))
    parts = str(entry).split(':')
    return parts[1] if len(parts) > 1 else parts[0]


def _merge_volumes(
    base: Sequence[Any],
    override: Sequence[Any],
) -> list[Any]:
    merged = list(base)
    targets = {volume_target(entry): index
               for index, entry in enumerate(merged)}
    for entry in override:
        target = volume_target(entry)
        index = targets.get(target)
        if index is None:
            targets[target] = len(merged)
            merged.append(entry)
        else:
            merged[index] = entry
    return merged


def _merge_sequence(
    key: str,
    base: Sequence[Any],
    override: Sequence[Any],
) -> list[Any]:
    if key == 'volumes':
        return _merge_volumes(base, override)
    merged = list(base)
    for entry in override:
        if entry not in merged:
            merged.append(entry)
    return merged


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge(current, value)
        elif (
            isinstance(current, list)
            and isinstance(value, list)
        ):
            merged[key] = _merge_sequence(key, current, value)
        else:
            merged[key] = value
    return merged


def render_compose(*paths: str) -> dict[str, Any]:
    """Merge Compose files the way ``docker compose -f ... -f ...`` does.

    The base file is prepended when it is not named first, so an overlay
    can be rendered on its own the way it is always deployed.
    """
    names = list(paths) or [BASE_COMPOSE_FILE]
    if names[0] != BASE_COMPOSE_FILE:
        names.insert(0, BASE_COMPOSE_FILE)
    rendered: dict[str, Any] = {}
    for name in names:
        rendered = _merge(rendered, _load(REPOSITORY_ROOT / name))
    return rendered


def published_ports(compose: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return the host port mappings each service publishes, if any."""
    mappings: dict[str, list[str]] = {}
    for name, service in compose.get('services', {}).items():
        ports = service.get('ports') if isinstance(service, Mapping) else None
        if not ports:
            continue
        mappings[name] = [_port_text(entry) for entry in ports]
    return mappings


def _port_text(entry: Any) -> str:
    if isinstance(entry, Mapping):
        host_ip = entry.get('host_ip', '')
        published = entry.get('published', '')
        target = entry.get('target', '')
        prefix = f'{host_ip}:' if host_ip else ''
        return f'{prefix}{published}:{target}'
    return str(entry)


def shared_browser_stage(dockerfile: str) -> str:
    """Return the shared browser runtime block of one Dockerfile.

    Docker has no include directive, so the stage is duplicated verbatim
    between the two images and kept honest by comparing these blocks.
    """
    if SHARED_STAGE_BEGIN not in dockerfile:
        return ''
    _, _, remainder = dockerfile.partition(SHARED_STAGE_BEGIN)
    block, separator, _ = remainder.partition(SHARED_STAGE_END)
    return block.strip() if separator else ''


def _requirement_pins(relative_path: str) -> dict[str, str]:
    path = REPOSITORY_ROOT / relative_path
    pins: dict[str, str] = {}
    if not path.is_file():
        return pins
    for line in path.read_text(encoding='utf-8').splitlines():
        entry = line.strip()
        if not entry or entry.startswith('#') or '==' not in entry:
            continue
        name, _, version = entry.partition('==')
        pins[name.strip().lower()] = version.strip()
    return pins


def _check_profiles(
    services: Mapping[str, Any],
    compose: Mapping[str, Any],
) -> Iterable[str]:
    declared = compose.get('volumes') or {}
    if LEGACY_PROFILE_VOLUME in declared:
        yield (
            f'volume {LEGACY_PROFILE_VOLUME!r} predates the per-role '
            'profile layout and must be removed'
        )
    mounted: dict[str, str] = {}
    for name in BROWSER_SERVICES:
        service = services.get(name, {})
        expected = f'{PROFILE_VOLUMES[name]}:{BROWSER_PROFILE_TARGET}'
        volumes = [str(entry) for entry in service.get('volumes', [])]
        if expected not in volumes:
            yield f'service {name!r} must mount {expected!r}'
            continue
        mounted[name] = PROFILE_VOLUMES[name]
        if PROFILE_VOLUMES[name] not in declared:
            yield (
                f'named volume {PROFILE_VOLUMES[name]!r} is mounted but '
                'never declared'
            )
    if len(set(mounted.values())) != len(mounted):
        yield 'api and bot must never share one browser profile volume'


def _check_roles(services: Mapping[str, Any]) -> Iterable[str]:
    for name in BROWSER_SERVICES:
        environment = services.get(name, {}).get('environment', {})
        if environment.get('WEB_CONCURRENCY') != '1':
            yield f'service {name!r} must set WEB_CONCURRENCY=1'
        if environment.get('MARKETPLACE_RUNTIME_ROLE') != RUNTIME_ROLES[name]:
            yield (
                f'service {name!r} must set MARKETPLACE_RUNTIME_ROLE='
                f'{RUNTIME_ROLES[name]}'
            )
        if environment.get('BROWSER_PROFILE_ROOT') != BROWSER_PROFILE_TARGET:
            yield (
                f'service {name!r} must set BROWSER_PROFILE_ROOT='
                f'{BROWSER_PROFILE_TARGET}'
            )


def _check_hardening(services: Mapping[str, Any]) -> Iterable[str]:
    for name in BROWSER_SERVICES:
        service = services.get(name, {})
        user = str(service.get('user', ''))
        if not user or user in ('root', '0', '0:0') or user.startswith('0:'):
            yield f'service {name!r} must run as a non-root user'
        if not service.get('shm_size'):
            yield f'service {name!r} must size /dev/shm for Chromium'
        options = [str(entry) for entry in service.get('security_opt', [])]
        if not any(
            option.startswith('seccomp') and SECCOMP_PROFILE_FILE in option
            for option in options
        ):
            yield (
                f'service {name!r} must apply {SECCOMP_PROFILE_FILE} as its '
                'seccomp profile'
            )


def _check_exposure(compose: Mapping[str, Any]) -> Iterable[str]:
    for name, ports in published_ports(compose).items():
        for port in ports:
            if name in PRIVATE_SERVICES and not _is_loopback(port):
                yield (
                    f'service {name!r} may only publish on 127.0.0.1, '
                    f'got {port!r}'
                )
            for control_port in BROWSER_CONTROL_PORTS:
                if control_port in port.split(':'):
                    yield (
                        f'service {name!r} publishes browser-control port '
                        f'{port!r}'
                    )


def _is_loopback(port: str) -> bool:
    return port.startswith('127.0.0.1:') or port.startswith('localhost:')


def _check_images() -> Iterable[str]:
    stages: dict[str, str] = {}
    for name in BROWSER_IMAGES:
        path = REPOSITORY_ROOT / name
        if not path.is_file():
            yield f'{name} is missing'
            continue
        content = path.read_text(encoding='utf-8')
        stage = shared_browser_stage(content)
        if not stage:
            yield f'{name} has no shared browser runtime stage'
        stages[name] = stage
        if 'ENTRYPOINT ["tini", "--"]' not in content:
            yield f'{name} must use tini as its init process'
        if 'xvfb-run' not in content:
            yield f'{name} must run its headed browsers under Xvfb'
        users = [
            line.split(maxsplit=1)[1].strip()
            for line in content.splitlines()
            if line.startswith('USER ')
        ]
        if not users or users[-1] in ('root', '0'):
            yield f'{name} must end as a non-root user'
    if len(set(stages.values())) > 1:
        yield 'Dockerfile.api and Dockerfile.bot browser stages have drifted'


def _check_pins() -> Iterable[str]:
    main = _requirement_pins(MAIN_REQUIREMENTS_FILE)
    vendor = _requirement_pins(VENDOR_REQUIREMENTS_FILE)
    for package in ('playwright', 'patchright'):
        if package not in main:
            yield f'{MAIN_REQUIREMENTS_FILE} must pin {package}'
    main_playwright = main.get('playwright')
    vendor_playwright = vendor.get('playwright')
    if vendor_playwright and main_playwright == vendor_playwright:
        yield (
            'the main runtime and the vendored OhMyCaptcha snapshot must '
            'keep separate playwright pins'
        )


def _check_seccomp() -> Iterable[str]:
    path = REPOSITORY_ROOT / SECCOMP_PROFILE_FILE
    if not path.is_file():
        yield f'{SECCOMP_PROFILE_FILE} is missing'
        return
    profile = json.loads(path.read_text(encoding='utf-8'))
    if profile.get('defaultAction') != 'SCMP_ACT_ERRNO':
        yield f'{SECCOMP_PROFILE_FILE} must deny unlisted syscalls'
    allowed = {
        name
        for rule in profile.get('syscalls', [])
        if rule.get('action') == 'SCMP_ACT_ALLOW'
        for name in rule.get('names', [])
    }
    missing = {'clone', 'setns', 'unshare'} - allowed
    if missing:
        yield (
            f'{SECCOMP_PROFILE_FILE} must allow user namespace syscalls: '
            f'{sorted(missing)}'
        )


def check_policy(compose: Mapping[str, Any]) -> list[str]:
    """Return every deployment policy violation in a rendered Compose."""
    services = compose.get('services') or {}
    violations: list[str] = []
    violations.extend(_check_profiles(services, compose))
    violations.extend(_check_roles(services))
    violations.extend(_check_hardening(services))
    violations.extend(_check_exposure(compose))
    violations.extend(_check_images())
    violations.extend(_check_pins())
    violations.extend(_check_seccomp())
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Render the requested Compose files and report policy violations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'compose_files',
        nargs='*',
        default=[BASE_COMPOSE_FILE, PRODUCTION_COMPOSE_FILE],
        help='Compose files to merge, in override order.',
    )
    arguments = parser.parse_args(argv)
    compose = render_compose(*arguments.compose_files)
    violations = check_policy(compose)
    rendered = ' + '.join(arguments.compose_files)
    if violations:
        print(f'Compose policy violations in {rendered}:', file=sys.stderr)
        for violation in violations:
            print(f'  - {violation}', file=sys.stderr)
        return 1
    print(f'Compose policy verified for {rendered}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
