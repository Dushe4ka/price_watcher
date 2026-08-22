"""Check that Git and Docker inputs do not include local sensitive data.

Run from the repository root:
    python -m scripts.repository_hygiene
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


VIRTUAL_ENVIRONMENT_DIRECTORIES = frozenset(
    {
        '.venv',
        '.bot_venv',
        '.backend_venv',
        'env',
        'venv',
        'ENV',
        'env.bak',
        'venv.bak',
    }
)
TOOL_CACHE_DIRECTORIES = frozenset({'.pytest_cache', '.ruff_cache'})
RUNTIME_PROFILE_DIRECTORIES = frozenset(
    {
        '.browser-profile',
        '.browser-profiles',
        '.ozon-profile',
        '.wb-profile',
        '.yandex-market-profile',
        'browser-profiles',
        'profile_default',
    }
)
GRAPH_ARTIFACT_DIRECTORIES = frozenset({'graphify-out'})
BROWSER_PROFILE_ROLES = frozenset({'local', 'api', 'bot'})
BROWSER_PROFILE_MARKETPLACES = frozenset(
    {'wildberries', 'ozon', 'yandex_market'}
)
ENVIRONMENT_VARIANT_PATTERNS = frozenset(
    {'.env.*', '.env*', '**/.env.*', '**/.env*'}
)


@dataclass(frozen=True)
class Violation:
    """A repository input that must not reach version control or Docker."""

    kind: str
    path: str
    message: str


def scan_repository(repository: Path) -> list[Violation]:
    """Return policy violations for the supplied Git repository."""
    repository = repository.resolve()
    violations = _scan_tracked_files(repository)
    dockerfiles = sorted(
        path
        for path in repository.rglob('Dockerfile*')
        if path.is_file() and '.git' not in path.parts
    )
    violations.extend(_scan_dockerfiles(repository, dockerfiles))
    return violations


def _scan_tracked_files(repository: Path) -> list[Violation]:
    tracked_files = subprocess.run(
        ['git', '-C', str(repository), 'ls-files', '-z'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split('\0')
    violations: list[Violation] = []
    for raw_path in tracked_files:
        if not raw_path:
            continue
        path = PurePosixPath(raw_path)
        violation = _tracked_artifact_violation(path)
        if violation is not None:
            violations.append(violation)
    return violations


def _tracked_artifact_violation(path: PurePosixPath) -> Violation | None:
    name = path.name
    normalized_path = path.as_posix()
    if _is_environment_file(name):
        return Violation(
            kind='tracked_environment_file',
            path=normalized_path,
            message=(
                'Environment files must stay local; commit only .env.example.'
            ),
        )
    if 'node_modules' in path.parts:
        return Violation(
            kind='tracked_dependency_directory',
            path=normalized_path,
            message=(
                'Dependency directories must be restored by the package '
                'manager.'
            ),
        )
    if _contains_directory(path, VIRTUAL_ENVIRONMENT_DIRECTORIES):
        return Violation(
            kind='tracked_virtual_environment',
            path=normalized_path,
            message='Virtual environments must not be tracked.',
        )
    if '.sqlite' in name:
        return Violation(
            kind='tracked_sqlite_artifact',
            path=normalized_path,
            message='SQLite runtime artifacts must not be tracked.',
        )
    if name.endswith('.db'):
        return Violation(
            kind='tracked_database_artifact',
            path=normalized_path,
            message='Database runtime artifacts must not be tracked.',
        )
    if _contains_directory(path, TOOL_CACHE_DIRECTORIES):
        return Violation(
            kind='tracked_tool_cache',
            path=normalized_path,
            message='Tool caches must not be tracked.',
        )
    if _contains_directory(path, RUNTIME_PROFILE_DIRECTORIES):
        return Violation(
            kind='tracked_runtime_profile',
            path=normalized_path,
            message='Runtime browser profiles must not be tracked.',
        )
    if _is_browser_profile_artifact(path):
        return Violation(
            kind='tracked_runtime_profile',
            path=normalized_path,
            message='Runtime browser profiles must not be tracked.',
        )
    if _contains_directory(path, GRAPH_ARTIFACT_DIRECTORIES):
        return Violation(
            kind='tracked_graph_artifact',
            path=normalized_path,
            message='Generated graph artifacts must not be tracked.',
        )
    if '__pycache__' in path.parts or name.endswith(('.pyc', '.pyo')):
        return Violation(
            kind='tracked_python_bytecode',
            path=normalized_path,
            message='Python bytecode must not be tracked.',
        )
    if name == '.DS_Store':
        return Violation(
            kind='tracked_os_metadata',
            path=normalized_path,
            message='Operating-system metadata must not be tracked.',
        )
    return None


def _scan_dockerfiles(
    repository: Path,
    dockerfiles: Sequence[Path],
) -> list[Violation]:
    if not dockerfiles:
        return []

    violations: list[Violation] = []
    for dockerfile in dockerfiles:
        relative_path = dockerfile.relative_to(repository).as_posix()
        content = dockerfile.read_text(encoding='utf-8')
        for source in _docker_copy_sources(content):
            if _is_environment_source(source):
                violations.append(
                    Violation(
                        kind='docker_environment_copy',
                        path=relative_path,
                        message=(
                            'Docker COPY/ADD must not place environment files '
                            'in image layers.'
                        ),
                    )
                )
                break

    exposed_names = _environment_files_in_context(repository / '.dockerignore')
    if exposed_names:
        violations.append(
            Violation(
                kind='docker_environment_context',
                path='.dockerignore',
                message=(
                    'Docker build context does not exclude: '
                    f"{', '.join(exposed_names)}."
                ),
            )
        )
    return violations


def _docker_copy_sources(content: str) -> Iterable[str]:
    """Yield local source operands from COPY and ADD Docker instructions."""
    for instruction in _docker_instructions(content):
        match = re.match(r'^(?:COPY|ADD)\s+(.+)$', instruction, re.IGNORECASE)
        if match is None:
            continue
        arguments = match.group(1).strip()
        if arguments.startswith('['):
            try:
                operands = json.loads(arguments)
            except json.JSONDecodeError:
                continue
            if isinstance(operands, list) and all(
                isinstance(value, str) for value in operands
            ):
                yield from operands[:-1]
            continue

        operands = shlex.split(arguments, comments=True)
        sources = [
            operand
            for operand in operands[:-1]
            if not operand.startswith('--')
        ]
        yield from sources


def _docker_instructions(content: str) -> Iterable[str]:
    """Join backslash-continued Dockerfile instructions."""
    pending = ''
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.endswith('\\'):
            pending += f'{line[:-1]} '
            continue
        yield f'{pending}{line}'
        pending = ''
    if pending:
        yield pending


def _environment_files_in_context(dockerignore: Path) -> list[str]:
    rules = _dockerignore_rules(dockerignore)
    exposed_names: list[str] = []
    if not _is_dockerignore_excluded('.env', rules):
        exposed_names.append('.env')
    if not _dockerignore_excludes_environment_variants(rules):
        exposed_names.append('.env.*')
    return exposed_names


def _dockerignore_excludes_environment_variants(rules: Sequence[str]) -> bool:
    excluded = False
    for rule in rules:
        negated = rule.startswith('!')
        pattern = rule[1:] if negated else rule
        normalized = pattern.strip('/').rstrip('/')
        if normalized in ENVIRONMENT_VARIANT_PATTERNS:
            excluded = not negated
        elif negated and _is_environment_file(PurePosixPath(normalized).name):
            excluded = False
    return excluded


def _dockerignore_rules(dockerignore: Path) -> list[str]:
    if not dockerignore.is_file():
        return []
    return [
        line.strip()
        for line in dockerignore.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]


def _is_dockerignore_excluded(path: str, rules: Sequence[str]) -> bool:
    excluded = False
    for rule in rules:
        negated = rule.startswith('!')
        pattern = rule[1:] if negated else rule
        if _dockerignore_rule_matches(path, pattern):
            excluded = not negated
    return excluded


def _dockerignore_rule_matches(path: str, pattern: str) -> bool:
    normalized = pattern.strip('/').rstrip('/')
    if not normalized:
        return False
    return (
        fnmatchcase(path, normalized)
        or normalized == f'**/{path}'
        or normalized == path
    )


def _is_environment_file(name: str) -> bool:
    return name == '.env' or (
        name.startswith('.env.') and name != '.env.example'
    )


def _is_environment_source(source: str) -> bool:
    normalized = source.removeprefix('./').rstrip('/')
    return _is_environment_file(PurePosixPath(normalized).name)


def _contains_directory(
    path: PurePosixPath,
    directory_names: frozenset[str],
) -> bool:
    return bool(directory_names.intersection(path.parts))


def _is_browser_profile_artifact(path: PurePosixPath) -> bool:
    parts = path.parts
    return any(
        parts[index] in BROWSER_PROFILE_ROLES
        and parts[index + 1] in BROWSER_PROFILE_MARKETPLACES
        for index in range(len(parts) - 2)
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Check tracked artifacts and Docker environment-file exposure.'
        ),
    )
    parser.add_argument(
        '--repository',
        type=Path,
        default=Path.cwd(),
        help='Git repository to scan (default: current directory).',
    )
    parser.add_argument(
        '--format',
        choices=('text', 'json'),
        default='text',
        help='Output format (default: text).',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Shortcut for --format json.',
    )
    return parser.parse_args()


def main() -> int:
    """Run the command-line hygiene check."""
    arguments = parse_arguments()
    violations = scan_repository(arguments.repository)
    if arguments.json or arguments.format == 'json':
        report = {'violations': [asdict(item) for item in violations]}
        print(json.dumps(report))
    elif violations:
        for violation in violations:
            print(f'{violation.kind}: {violation.path} — {violation.message}')
    else:
        print('Repository hygiene check passed.')
    return int(bool(violations))


if __name__ == '__main__':
    raise SystemExit(main())
