from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from src.captcha.ohmycaptcha_adapter import (
    OhMyCaptchaAdapter,
    PINNED_UPSTREAM_COMMIT,
    VendorContractError,
    _vendor_namespace,
)


REPOSITORY_ROOT = Path(__file__).parents[1]
VENDOR_ROOT = REPOSITORY_ROOT / 'vendor' / 'ohmycaptcha'


class OhMyCaptchaAdapterTests(unittest.TestCase):
    def test_vendor_loader_does_not_replace_application_src(self) -> None:
        import src

        application_src = src
        original_path = tuple(sys.path)
        adapter = OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)

        adapter.vendor_scripts()

        self.assertIs(application_src, sys.modules['src'])
        self.assertEqual(original_path, tuple(sys.path))
        self.assertNotIn('src.services.recaptcha_v2', sys.modules)

    def test_exposes_only_reviewed_vendor_javascript_primitives(
        self,
    ) -> None:
        scripts = OhMyCaptchaAdapter(
            vendor_root=VENDOR_ROOT
        ).vendor_scripts()
        expected_markers = {
            'recaptcha_v2_extract': (
                '#g-recaptcha-response',
                'grecaptcha?.enterprise',
            ),
            'recaptcha_v3_execute': (
                'gr.execute(key, {action})',
                'document.head.appendChild(script)',
            ),
            'hcaptcha_extract': (
                '[name="h-captcha-response"]',
                'window.hcaptcha.getResponse',
            ),
            'turnstile_extract': (
                '[name="cf-turnstile-response"]',
                'window.turnstile.getResponse',
            ),
        }

        self.assertEqual(set(expected_markers), set(scripts))
        for script_name, markers in expected_markers.items():
            with self.subTest(script_name=script_name):
                self.assertIsInstance(scripts[script_name], str)
                for marker in markers:
                    self.assertIn(marker, scripts[script_name])

    def test_contract_is_pinned_to_reviewed_snapshot_and_versions(
        self,
    ) -> None:
        metadata = (VENDOR_ROOT / 'UPSTREAM.md').read_text(encoding='utf-8')
        main_requirements = (REPOSITORY_ROOT / 'requirements.txt').read_text(
            encoding='utf-8'
        )
        vendor_requirements = (VENDOR_ROOT / 'requirements.txt').read_text(
            encoding='utf-8'
        )

        self.assertIn(PINNED_UPSTREAM_COMMIT, metadata)
        self.assertIn('playwright==1.53.0', main_requirements)
        self.assertIn('patchright==1.61.2', main_requirements)
        self.assertIn('playwright==1.49.1', vendor_requirements)

    def test_fails_with_safe_error_when_vendor_metadata_drifts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            vendor_root = Path(temporary_directory)
            (vendor_root / 'UPSTREAM.md').write_text(
                '- Imported commit: `unexpected-snapshot`\n',
                encoding='utf-8',
            )

            with self.assertRaisesRegex(
                VendorContractError,
                '^pinned vendor contract is unavailable$',
            ):
                OhMyCaptchaAdapter(
                    vendor_root=vendor_root
                ).vendor_scripts()

    def test_adapter_repr_does_not_expose_vendor_path(self) -> None:
        adapter = OhMyCaptchaAdapter(
            vendor_root=Path('/SENTINEL_PRODUCT_IDENTIFIER')
        )

        self.assertNotIn('SENTINEL_PRODUCT_IDENTIFIER', repr(adapter))

    def test_os_error_is_not_retained_as_exception_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            adapter = OhMyCaptchaAdapter(
                vendor_root=Path(temporary_directory) / 'missing'
            )

            with self.assertRaises(VendorContractError) as raised:
                adapter.vendor_scripts()

        self.assertEqual(
            'pinned vendor contract is unavailable',
            str(raised.exception),
        )
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)

    def test_json_import_error_is_not_retained_as_context(self) -> None:
        with temporary_vendor_package() as vendor_root:
            adapter = OhMyCaptchaAdapter(vendor_root=vendor_root)
            error = json.JSONDecodeError(
                'SENTINEL_JSON_ERROR',
                'SENTINEL_DOCUMENT',
                0,
            )

            with patch(
                'src.captcha.ohmycaptcha_adapter.importlib.import_module',
                side_effect=error,
            ):
                with self.assertRaises(VendorContractError) as raised:
                    adapter.vendor_scripts()

        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn('SENTINEL', repr(raised.exception))

    def test_marker_drift_error_has_no_private_context(self) -> None:
        with temporary_vendor_package() as vendor_root:
            adapter = OhMyCaptchaAdapter(vendor_root=vendor_root)
            drifted_module = SimpleNamespace(
                _EXTRACT_TOKEN_JS='SENTINEL_DRIFTED_SCRIPT'
            )

            with patch(
                'src.captcha.ohmycaptcha_adapter.importlib.import_module',
                return_value=drifted_module,
            ):
                with self.assertRaises(VendorContractError) as raised:
                    adapter.vendor_scripts()

        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn('SENTINEL', repr(raised.exception))

    def test_completeness_marker_failure_is_sanitized(self) -> None:
        with temporary_vendor_package(include_services=True) as vendor_root:
            adapter = OhMyCaptchaAdapter(vendor_root=vendor_root)

            with patch(
                'src.captcha.ohmycaptcha_adapter.'
                '_mark_namespace_complete',
                side_effect=RuntimeError('SENTINEL_COMPLETE_MARKER'),
            ):
                try:
                    adapter.vendor_scripts()
                except BaseException as error:
                    captured = error
                else:
                    self.fail('expected a safe vendor contract failure')

        self.assertIsInstance(captured, VendorContractError)
        self.assertIsNone(captured.__context__)
        self.assertIsNone(captured.__cause__)
        self.assertNotIn('SENTINEL_COMPLETE_MARKER', repr(captured))

    def test_root_import_error_has_no_raw_exception_context(self) -> None:
        with temporary_vendor_package(
            init_source="raise RuntimeError('SENTINEL_ROOT_IMPORT')\n"
        ) as vendor_root:
            with self.assertRaises(VendorContractError) as raised:
                OhMyCaptchaAdapter(
                    vendor_root=vendor_root
                ).vendor_scripts()

        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertNotIn('SENTINEL_ROOT_IMPORT', repr(raised.exception))

    def test_keyboard_interrupt_cleans_synthetic_namespace(self) -> None:
        with temporary_vendor_package(
            init_source='raise KeyboardInterrupt\n'
        ) as vendor_root:
            namespace = _vendor_namespace(vendor_root.resolve())

            with self.assertRaises(KeyboardInterrupt):
                OhMyCaptchaAdapter(
                    vendor_root=vendor_root
                ).vendor_scripts()

            self.assertNotIn(namespace, sys.modules)
            self.assertFalse(
                any(
                    name.startswith(f'{namespace}.')
                    for name in sys.modules
                )
            )

    def test_partial_cached_namespace_is_purged_and_reloaded(self) -> None:
        with temporary_vendor_package(include_services=True) as vendor_root:
            namespace = _vendor_namespace(vendor_root.resolve())
            partial = ModuleType(namespace)
            sys.modules[namespace] = partial
            try:
                try:
                    scripts = OhMyCaptchaAdapter(
                        vendor_root=vendor_root
                    ).vendor_scripts()
                except VendorContractError:
                    scripts = {}

                self.assertEqual(
                    {
                        'recaptcha_v2_extract',
                        'recaptcha_v3_execute',
                        'hcaptcha_extract',
                        'turnstile_extract',
                    },
                    set(scripts),
                )
                self.assertIsNot(partial, sys.modules[namespace])
            finally:
                for name in tuple(sys.modules):
                    if name == namespace or name.startswith(
                        f'{namespace}.'
                    ):
                        sys.modules.pop(name, None)


@contextmanager
def temporary_vendor_package(
    *,
    init_source: str = '',
    include_services: bool = False,
) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        source = root / 'src'
        source.mkdir()
        (source / '__init__.py').write_text(
            init_source,
            encoding='utf-8',
        )
        (root / 'UPSTREAM.md').write_text(
            f'- Imported commit: `{PINNED_UPSTREAM_COMMIT}`\n',
            encoding='utf-8',
        )
        if include_services:
            write_vendor_service_fixtures(source)
        yield root


def write_vendor_service_fixtures(source: Path) -> None:
    services = source / 'services'
    services.mkdir()
    (services / '__init__.py').write_text('', encoding='utf-8')
    scripts = {
        'recaptcha_v2.py': (
            '_EXTRACT_TOKEN_JS = '
            "'#g-recaptcha-response grecaptcha?.enterprise'\n"
        ),
        'recaptcha_v3.py': (
            '_EXECUTE_JS = '
            "'gr.execute(key, {action}) document.head.appendChild(script)'\n"
        ),
        'hcaptcha.py': (
            '_EXTRACT_HCAPTCHA_TOKEN_JS = '
            "'[name=\"h-captcha-response\"] "
            "window.hcaptcha.getResponse'\n"
        ),
        'turnstile.py': (
            '_EXTRACT_TURNSTILE_TOKEN_JS = '
            "'[name=\"cf-turnstile-response\"] "
            "window.turnstile.getResponse'\n"
        ),
    }
    for filename, content in scripts.items():
        (services / filename).write_text(content, encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
