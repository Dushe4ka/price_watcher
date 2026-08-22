from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from src.captcha.ohmycaptcha_adapter import (
    OhMyCaptchaAdapter,
    PINNED_UPSTREAM_COMMIT,
    VendorContractError,
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


if __name__ == '__main__':
    unittest.main()
