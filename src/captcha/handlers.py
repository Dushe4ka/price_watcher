"""Conservative same-page challenge handlers."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

from src.browser.contracts import FrameLike, FrameLocatorLike, PageLike
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

_FRAME_TITLE_SELECTORS = {
    ChallengeType.RECAPTCHA_V2: 'iframe[title="reCAPTCHA"]',
    ChallengeType.HCAPTCHA: (
        'iframe[title="Widget containing checkbox for '
        'hCaptcha security challenge"]'
    ),
    ChallengeType.TURNSTILE: (
        'iframe[title="Widget containing a Cloudflare security challenge"]'
    ),
}

_CHECKBOX_SELECTORS = {
    ChallengeType.RECAPTCHA_V2: '#recaptcha-anchor',
    ChallengeType.HCAPTCHA: '#checkbox',
    ChallengeType.TURNSTILE: (
        'input[type="checkbox"], [role="checkbox"], '
        '.ctp-checkbox-label'
    ),
}


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
    frame = next(
        (
            candidate
            for candidate in page.frames
            if _matches_provider_frame(candidate, challenge_type)
        ),
        None,
    )
    owner: FrameLike | FrameLocatorLike
    if frame is not None:
        owner = frame
    else:
        iframe = page.locator(_FRAME_TITLE_SELECTORS[challenge_type])
        source = await iframe.get_attribute('src', timeout=timeout_ms)
        if not _matches_provider_url(source, challenge_type):
            raise RuntimeError('provider frame ownership is unavailable')
        owner = iframe.content_frame
    checkbox = owner.locator(_CHECKBOX_SELECTORS[challenge_type])
    await checkbox.click(timeout=timeout_ms)


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
