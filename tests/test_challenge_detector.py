from __future__ import annotations

import unittest
from pathlib import Path

from src.captcha.detector import detect_challenge
from src.captcha.models import ChallengeType


FIXTURES = Path(__file__).parent / 'fixtures'


class FixturePage:
    def __init__(self, html: str) -> None:
        self.html = html

    async def content(self) -> str:
        return self.html


def load_fixture(path: str) -> str:
    return (FIXTURES / path).read_text(encoding='utf-8')


class ChallengeDetectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_detects_each_supported_challenge_and_clean_page(
        self,
    ) -> None:
        cases = (
            ('clean.html', ChallengeType.NONE),
            ('recaptcha-v2.html', ChallengeType.RECAPTCHA_V2),
            ('recaptcha-v3.html', ChallengeType.RECAPTCHA_V3),
            ('hcaptcha.html', ChallengeType.HCAPTCHA),
            ('turnstile.html', ChallengeType.TURNSTILE),
            ('unknown.html', ChallengeType.UNKNOWN),
        )

        for fixture_name, expected_type in cases:
            with self.subTest(fixture_name=fixture_name):
                page = FixturePage(
                    load_fixture(f'challenges/{fixture_name}')
                )
                detection = await detect_challenge(page)

                self.assertIs(expected_type, detection.challenge_type)

    async def test_uses_deterministic_priority_for_overlapping_markers(
        self,
    ) -> None:
        markers = (
            load_fixture('challenges/unknown.html'),
            load_fixture('challenges/turnstile.html'),
            load_fixture('challenges/hcaptcha.html'),
            load_fixture('challenges/recaptcha-v3.html'),
            load_fixture('challenges/recaptcha-v2.html'),
        )
        expected = (
            ChallengeType.UNKNOWN,
            ChallengeType.TURNSTILE,
            ChallengeType.HCAPTCHA,
            ChallengeType.RECAPTCHA_V3,
            ChallengeType.RECAPTCHA_V2,
        )
        html = ''

        for marker, expected_type in zip(markers, expected, strict=True):
            html += marker
            with self.subTest(expected_type=expected_type):
                detection = await detect_challenge(FixturePage(html))

                self.assertIs(expected_type, detection.challenge_type)

    async def test_marks_interactive_challenge_without_exposing_content(
        self,
    ) -> None:
        secret_html = (
            '<iframe title="recaptcha challenge"></iframe>'
            '<div class="g-recaptcha">SENTINEL_BODY_TOKEN</div>'
        )

        detection = await detect_challenge(FixturePage(secret_html))

        self.assertIs(ChallengeType.RECAPTCHA_V2, detection.challenge_type)
        self.assertTrue(detection.is_interactive)
        self.assertNotIn('SENTINEL_BODY_TOKEN', repr(detection))

    async def test_marks_hcaptcha_image_challenge_as_interactive(
        self,
    ) -> None:
        html = (
            '<div class="h-captcha"></div>'
            '<iframe title="Main content of the hCaptcha challenge">'
            '</iframe>'
        )

        detection = await detect_challenge(FixturePage(html))

        self.assertIs(ChallengeType.HCAPTCHA, detection.challenge_type)
        self.assertTrue(detection.is_interactive)

    async def test_provider_names_in_plain_text_are_not_a_challenge(
        self,
    ) -> None:
        html = (
            '<article>Comparison of reCAPTCHA v2, reCAPTCHA v3, '
            'hCaptcha, and Turnstile providers.</article>'
        )

        detection = await detect_challenge(FixturePage(html))

        self.assertIs(ChallengeType.NONE, detection.challenge_type)


if __name__ == '__main__':
    unittest.main()
