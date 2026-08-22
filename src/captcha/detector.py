"""Deterministic, content-discarding challenge detection."""

from __future__ import annotations

import re

from src.browser.contracts import PageLike
from src.captcha.models import ChallengeDetection, ChallengeType


_TYPE_MARKERS = (
    (
        ChallengeType.RECAPTCHA_V2,
        (
            'class="g-recaptcha"',
            "class='g-recaptcha'",
            'recaptcha/api2/anchor',
            'id="recaptcha-anchor"',
            'id=\'recaptcha-anchor\'',
            '#g-recaptcha-response',
        ),
    ),
    (
        ChallengeType.RECAPTCHA_V3,
        (
            'recaptcha/api.js?render=',
            'grecaptcha.execute',
        ),
    ),
    (
        ChallengeType.HCAPTCHA,
        (
            'class="h-captcha"',
            "class='h-captcha'",
            'hcaptcha.com/1/api.js',
            'h-captcha-response',
        ),
    ),
    (
        ChallengeType.TURNSTILE,
        (
            'class="cf-turnstile"',
            "class='cf-turnstile'",
            'challenges.cloudflare.com/turnstile',
            'cf-turnstile-response',
        ),
    ),
)

_INTERACTIVE_MARKERS = (
    'recaptcha challenge',
    'image challenge',
    'audio challenge',
    'visual challenge',
    'main content of the hcaptcha challenge',
    'captcha slider',
    'slider challenge',
)

_UNKNOWN_ELEMENT = re.compile(
    r'(?:id|class|name)\s*=\s*["\'][^"\']*'
    r'(?:captcha|human-verification)[^"\']*["\']'
)

_UNKNOWN_TEXT_MARKERS = (
    'complete the captcha',
    'captcha challenge',
    'verify you are human',
    'confirm you are not a robot',
)


async def detect_challenge(page: PageLike) -> ChallengeDetection:
    """Classify the current page without retaining its serialized content."""
    html = (await page.content()).lower()

    for challenge_type, markers in _TYPE_MARKERS:
        if any(marker in html for marker in markers):
            return ChallengeDetection(
                challenge_type=challenge_type,
                is_interactive=_is_interactive(html),
            )

    if _UNKNOWN_ELEMENT.search(html) or any(
        marker in html for marker in _UNKNOWN_TEXT_MARKERS
    ):
        return ChallengeDetection(challenge_type=ChallengeType.UNKNOWN)

    return ChallengeDetection(challenge_type=ChallengeType.NONE)


def _is_interactive(html: str) -> bool:
    return any(marker in html for marker in _INTERACTIVE_MARKERS)
