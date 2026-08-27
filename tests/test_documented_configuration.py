"""Executable documentation contracts for the marketplace fallback stack.

These tests introspect the real published surface -- ``Settings``' own
marketplace-related model fields, the real ``SafeErrorCode`` and
``SourceOutcome`` enums -- and require the operator documentation to cover
it. Nothing here asserts a single hardcoded implementation string: add a
marketplace setting or a safe error code and the corresponding test fails
until the runbooks describe it.

The path test closes the other half of the loop: a runbook that names a
repository file has to name one that exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase

from pydantic_core import PydanticUndefined

from src.core.config import Settings
from src.marketplaces.contracts import SourceOutcome
from src.marketplaces.errors import SafeErrorCode


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPOSITORY_ROOT / 'docs'
RUNBOOKS_ROOT = DOCS_ROOT / 'runbooks'
ARCHITECTURE_ROOT = DOCS_ROOT / 'architecture'

#: A ``Settings`` field belongs to the marketplace fallback surface when its
#: name carries one of these prefixes or suffixes. Derived from the field
#: names themselves so a newly added setting is covered automatically.
_SETTING_PREFIXES = (
    'apify_',
    'browser_profile_',
    'captcha_',
    'marketplace_',
    'ohmycaptcha_',
    'smartcaptcha_',
)
_SETTING_SUFFIXES = ('_source_chain',)

#: Extensions a slash-free token has to carry before it is treated as a
#: repository file reference rather than prose or a dotted module path.
_REPOSITORY_FILE_SUFFIXES = frozenset(
    {
        '.api',
        '.bot',
        '.cfg',
        '.example',
        '.ini',
        '.json',
        '.md',
        '.py',
        '.sh',
        '.txt',
        '.yaml',
        '.yml',
    }
)

_INLINE_CODE = re.compile(r'`([^`\n]+)`')
_FENCED_BLOCK = re.compile(r'^```[^\n]*\n(.*?)^```', re.DOTALL | re.MULTILINE)
_PATH_TOKEN = re.compile(r'^[A-Za-z0-9_.][A-Za-z0-9_./-]*$')


def documentation_files() -> tuple[Path, ...]:
    """Return every runbook and architecture page, in a stable order."""
    pages: list[Path] = []
    for root in (RUNBOOKS_ROOT, ARCHITECTURE_ROOT):
        if root.is_dir():
            pages.extend(sorted(root.glob('*.md')))
    return tuple(pages)


def read_all_runbooks() -> str:
    """Return the concatenated text of every operator runbook."""
    if not RUNBOOKS_ROOT.is_dir():
        return ''
    return '\n'.join(
        path.read_text(encoding='utf-8')
        for path in sorted(RUNBOOKS_ROOT.glob('*.md'))
    )


def read_all_documentation() -> str:
    """Return the concatenated text of runbooks and architecture pages."""
    return '\n'.join(
        path.read_text(encoding='utf-8') for path in documentation_files()
    )


def public_marketplace_setting_names() -> tuple[str, ...]:
    """Return the marketplace-related public ``Settings`` field names.

    Read from ``Settings.model_fields`` rather than from a list kept in this
    module, so the coverage requirement tracks the real configuration
    surface. Properties (``runtime_role``, ``apify_api_token``) are not
    model fields and are deliberately out of scope: they are derived views,
    not environment variables an operator sets.
    """
    return tuple(
        sorted(
            name
            for name in Settings.model_fields
            if name.startswith(_SETTING_PREFIXES)
            or name.endswith(_SETTING_SUFFIXES)
        )
    )


def documented_default(name: str) -> str | None:
    """Return the rendered default of one setting, or ``None`` when empty.

    An empty string, an empty secret and a factory-built default carry no
    value worth pinning in a table, so they are excluded from the default
    contract rather than documented as a literal blank.
    """
    field = Settings.model_fields[name]
    default = field.default
    if default is PydanticUndefined or default is None:
        return None
    if field.default_factory is not None:
        return None
    rendered = str(default)
    return rendered or None


def documented_local_paths() -> tuple[tuple[Path, str], ...]:
    """Return every repository-relative path the documentation names.

    Both inline code spans and fenced command blocks are scanned, so a path
    named only inside a copy-pasteable command is covered too. A token counts
    as a repository path when it is relative (so container paths such as
    ``/data/browser-profiles`` are excluded), is free of shell
    metacharacters, and either descends from a real top-level repository
    entry or carries a repository file extension.
    """
    root_entries = {entry.name for entry in REPOSITORY_ROOT.iterdir()}
    found: list[tuple[Path, str]] = []
    seen: set[tuple[str, str]] = set()
    for page in documentation_files():
        text = page.read_text(encoding='utf-8')
        for token in _documented_tokens(text):
            candidate = _path_candidate(token, root_entries)
            if candidate is None:
                continue
            key = (page.name, candidate)
            if key in seen:
                continue
            seen.add(key)
            found.append((page, candidate))
    return tuple(found)


def _documented_tokens(text: str) -> tuple[str, ...]:
    tokens = list(_INLINE_CODE.findall(text))
    for block in _FENCED_BLOCK.findall(text):
        tokens.extend(block.split())
    return tuple(tokens)


def _path_candidate(token: str, root_entries: set[str]) -> str | None:
    value = token.strip().rstrip('/')
    if not value or not _PATH_TOKEN.fullmatch(value):
        return None
    if value.startswith(('http:', 'https:', './', '../')):
        return None
    head = value.split('/', 1)[0]
    if '/' in value:
        return value if head in root_entries else None
    if Path(value).suffix in _REPOSITORY_FILE_SUFFIXES:
        return value
    return None


class DocumentedConfigurationTests(TestCase):
    """Require the runbooks to describe the real configuration surface."""

    def test_marketplace_settings_are_introspected_not_hardcoded(
        self,
    ) -> None:
        names = public_marketplace_setting_names()

        self.assertGreater(len(names), 15, names)
        self.assertIn('marketplace_total_timeout_sec', names)
        self.assertIn('marketplace_operation_timeout_sec', names)
        self.assertIn('ozon_source_chain', names)
        for name in names:
            self.assertIn(name, Settings.model_fields, name)

    def test_every_marketplace_setting_is_documented(self) -> None:
        documented = read_all_runbooks()

        for name in public_marketplace_setting_names():
            self.assertIn(name.upper(), documented, name)

    def test_every_marketplace_setting_is_in_the_env_example(self) -> None:
        example = (REPOSITORY_ROOT / '.env.example').read_text(
            encoding='utf-8',
        )

        for name in public_marketplace_setting_names():
            self.assertIn(f'{name.upper()}=', example, name)

    def test_documented_defaults_match_the_settings_defaults(self) -> None:
        rows = self._setting_table_rows()

        for name in public_marketplace_setting_names():
            default = documented_default(name)
            if default is None:
                continue
            matching = rows.get(name.upper(), ())
            self.assertTrue(matching, f'{name} has no documented table row')
            self.assertTrue(
                any(f'`{default}`' in row for row in matching),
                f'{name} is not documented with its default `{default}`',
            )

    def test_the_single_worker_requirement_is_documented(self) -> None:
        documented = read_all_runbooks()

        self.assertIn('WEB_CONCURRENCY', documented)
        self.assertIn('WEB_CONCURRENCY=1', documented)

    def test_every_safe_error_code_is_documented(self) -> None:
        documented = read_all_runbooks()

        for code in SafeErrorCode:
            self.assertIn(code.value, documented, code.value)

    def test_every_source_outcome_is_documented(self) -> None:
        documented = read_all_documentation()

        for outcome in SourceOutcome:
            self.assertIn(outcome.value, documented, outcome.value)

    def test_runbook_commands_reference_existing_files(self) -> None:
        paths = documented_local_paths()

        self.assertGreater(len(paths), 10, 'no documented paths were found')
        for page, path in paths:
            with self.subTest(page=page.name, path=path):
                self.assertTrue(
                    REPOSITORY_ROOT.joinpath(path).exists(),
                    f'{page.name} references missing path {path}',
                )

    def test_the_expected_documentation_pages_exist(self) -> None:
        for relative in (
            'docs/architecture/marketplace-fallback.md',
            'docs/runbooks/local-development.md',
            'docs/runbooks/troubleshooting.md',
            'docs/runbooks/vps-deployment.md',
        ):
            with self.subTest(page=relative):
                self.assertTrue(
                    REPOSITORY_ROOT.joinpath(relative).is_file(),
                    relative,
                )

    def _setting_table_rows(self) -> dict[str, tuple[str, ...]]:
        rows: dict[str, list[str]] = {}
        for line in read_all_runbooks().splitlines():
            stripped = line.strip()
            if not stripped.startswith('|'):
                continue
            for name in _INLINE_CODE.findall(stripped):
                if name.isupper():
                    rows.setdefault(name, []).append(stripped)
        return {name: tuple(value) for name, value in rows.items()}
