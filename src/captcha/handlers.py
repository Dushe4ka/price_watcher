"""Conservative same-page challenge handlers."""

from __future__ import annotations

import asyncio
import math
from typing import Protocol
from urllib.parse import urlsplit

from src.browser.contracts import FrameLike, PageLike
from src.captcha.models import ChallengeDetection, ChallengeType
from src.captcha.ohmycaptcha_adapter import OhMyCaptchaAdapter


class ChallengeHandler(Protocol):
    """A handler that may act only on its injected leased page."""

    def supports(self, detection: ChallengeDetection) -> bool:
        """Return whether this handler supports a deterministic flow."""

    async def handle(
        self,
        page: PageLike,
        detection: ChallengeDetection,
        *,
        timeout_ms: float,
    ) -> None:
        """Attempt the challenge on ``page`` without creating browser state."""


_RECAPTCHA_V3_TEMPLATE = """
() => {
    const widget = document.querySelector('[data-sitekey]');
    const key = widget?.getAttribute('data-sitekey');
    const action = widget?.getAttribute('data-action') || 'verify';
    if (!key) return null;
    const execute = (__VENDOR_EXECUTE__);
    return execute([key, action]).then((token) => {
        const callbackName = widget?.getAttribute('data-callback');
        const callback = callbackName ? window[callbackName] : null;
        if (typeof callback === 'function') callback(token);
        return null;
    });
}
"""

_FRAME_HOSTS = {
    ChallengeType.RECAPTCHA_V2: ('google.com', 'recaptcha.net'),
    ChallengeType.HCAPTCHA: ('hcaptcha.com',),
    ChallengeType.TURNSTILE: ('challenges.cloudflare.com',),
}

_CHECKBOX_SELECTORS = {
    ChallengeType.RECAPTCHA_V2: '#recaptcha-anchor',
    ChallengeType.HCAPTCHA: '#checkbox',
    ChallengeType.TURNSTILE: (
        'input[type="checkbox"], [role="checkbox"], '
        '.ctp-checkbox-label'
    ),
}

_FRAME_POLL_INTERVAL_SEC = 0.01


class OhMyCaptchaHandler:
    """Apply reviewed deterministic actions to the existing page only."""

    __slots__ = ('_adapter',)

    def __init__(self, adapter: OhMyCaptchaAdapter) -> None:
        self._adapter = adapter

    def supports(self, detection: ChallengeDetection) -> bool:
        return (
            not detection.is_interactive
            and detection.challenge_type
            in (
                ChallengeType.RECAPTCHA_V2,
                ChallengeType.RECAPTCHA_V3,
                ChallengeType.HCAPTCHA,
                ChallengeType.TURNSTILE,
            )
        )

    async def handle(
        self,
        page: PageLike,
        detection: ChallengeDetection,
        *,
        timeout_ms: float,
    ) -> None:
        if not self.supports(detection):
            return
        scripts = self._adapter.vendor_scripts()
        if detection.challenge_type is ChallengeType.RECAPTCHA_V3:
            execute = scripts['recaptcha_v3_execute']
            expression = _RECAPTCHA_V3_TEMPLATE.replace(
                '__VENDOR_EXECUTE__',
                execute,
            )
        else:
            await _click_provider_checkbox(
                page,
                detection.challenge_type,
                timeout_ms,
            )
            return
        await page.evaluate(expression)

    def __repr__(self) -> str:
        return 'OhMyCaptchaHandler(mode=deterministic_same_page)'


async def _click_provider_checkbox(
    page: PageLike,
    challenge_type: ChallengeType,
    timeout_ms: float,
) -> None:
    if not math.isfinite(timeout_ms) or timeout_ms <= 0:
        raise RuntimeError('provider frame deadline is unavailable')
    loop = asyncio.get_running_loop()
    expires_at = loop.time() + (timeout_ms / 1000)
    frame = await _wait_for_provider_frame(
        page,
        challenge_type,
        expires_at,
    )
    if not _matches_provider_frame(frame, challenge_type):
        raise RuntimeError('provider frame ownership is unavailable')
    remaining_ms = (expires_at - loop.time()) * 1000
    if remaining_ms <= 0:
        raise RuntimeError('provider frame deadline is unavailable')
    checkbox = frame.locator(_CHECKBOX_SELECTORS[challenge_type])
    await checkbox.click(timeout=remaining_ms)


async def _wait_for_provider_frame(
    page: PageLike,
    challenge_type: ChallengeType,
    expires_at: float,
) -> FrameLike:
    loop = asyncio.get_running_loop()
    while True:
        frame = next(
            (
                candidate
                for candidate in page.frames
                if _matches_provider_frame(candidate, challenge_type)
            ),
            None,
        )
        if frame is not None:
            return frame
        remaining = expires_at - loop.time()
        if remaining <= 0:
            raise RuntimeError('provider frame ownership is unavailable')
        await asyncio.sleep(min(_FRAME_POLL_INTERVAL_SEC, remaining))


def _matches_provider_frame(
    frame: FrameLike,
    challenge_type: ChallengeType,
) -> bool:
    return _matches_provider_url(frame.url, challenge_type)


def _matches_provider_url(
    url: str | None,
    challenge_type: ChallengeType,
) -> bool:
    hosts = _FRAME_HOSTS[challenge_type]
    parsed = urlsplit(url or '')
    if parsed.scheme.lower() != 'https':
        return False
    hostname = (parsed.hostname or '').lower()
    if not any(
        hostname == host or hostname.endswith(f'.{host}')
        for host in hosts
    ):
        return False
    path = parsed.path.lower()
    if challenge_type is ChallengeType.RECAPTCHA_V2:
        return (
            path.startswith('/recaptcha/')
            and path.rstrip('/').endswith('/anchor')
        )
    if challenge_type is ChallengeType.HCAPTCHA:
        fragment_parts = frozenset(parsed.fragment.lower().split('&'))
        return 'hcaptcha' in path and 'frame=checkbox' in fragment_parts
    return path.startswith('/turnstile/')
