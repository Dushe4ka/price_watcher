"""Behavioral checks for the repository hygiene command.

Run: python -m unittest tests.test_repository_hygiene -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RepositoryHygieneCommandTests(unittest.TestCase):
    def test_reports_tracked_secret_and_generated_artifacts(self) -> None:
        with temporary_repository() as repository:
            write_file(repository, '.env', 'LOCAL_ONLY=value\n')
            write_file(repository, 'cache.sqlite-wal', '')
            write_file(repository, 'node_modules/package.json', '{}\n')
            track_all(repository)

            result, report = run_guard(repository)

        self.assertEqual(result.returncode, 1, result.stderr)
        violations = {
            (item['kind'], item['path']) for item in report['violations']
        }
        self.assertIn(('tracked_environment_file', '.env'), violations)
        self.assertIn(
            ('tracked_sqlite_artifact', 'cache.sqlite-wal'),
            violations,
        )
        self.assertIn(
            ('tracked_dependency_directory', 'node_modules/package.json'),
            violations,
        )

    def test_reports_environment_files_exposed_to_docker_build(self) -> None:
        with temporary_repository() as repository:
            write_file(repository, 'Dockerfile.api', 'COPY .env /app\n')
            write_file(repository, '.dockerignore', '__pycache__/\n')
            track_all(repository)

            result, report = run_guard(repository)

        self.assertEqual(result.returncode, 1, result.stderr)
        kinds = {item['kind'] for item in report['violations']}
        self.assertIn('docker_environment_copy', kinds)
        self.assertIn('docker_environment_context', kinds)

    def test_allows_sanitized_example_in_excluded_docker_context(self) -> None:
        with temporary_repository() as repository:
            write_file(repository, '.env.example', 'SECRET=replace_me\n')
            write_file(repository, 'Dockerfile.api', 'COPY src /app/src\n')
            write_file(
                repository,
                '.dockerignore',
                '.env\n.env.*\n!.env.example\n',
            )
            track_all(repository)

            result, report = run_guard(repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['violations'], [])


class TemporaryRepository:
    def __enter__(self) -> Path:
        self._directory = tempfile.TemporaryDirectory()
        self.path = Path(self._directory.name)
        subprocess.run(
            ['git', 'init', '--quiet'],
            cwd=self.path,
            check=True,
            capture_output=True,
            text=True,
        )
        return self.path

    def __exit__(self, *args: object) -> None:
        self._directory.cleanup()


def temporary_repository() -> TemporaryRepository:
    return TemporaryRepository()


def write_file(repository: Path, name: str, content: str) -> None:
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def track_all(repository: Path) -> None:
    subprocess.run(
        ['git', 'add', '.'],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def run_guard(
    repository: Path,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'scripts.repository_hygiene',
            '--repository',
            str(repository),
            '--format',
            'json',
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        report = {'violations': []}
    return result, report


if __name__ == '__main__':
    unittest.main()
