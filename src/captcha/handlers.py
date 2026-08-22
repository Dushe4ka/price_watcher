"""Conservative same-page challenge handlers."""

from __future__ import annotations

from typing import Protocol

from src.browser.contracts import PageLike
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
    ) -> None:
        """Attempt the challenge on ``page`` without creating browser state."""


_RECAPTCHA_V2_CHECKBOX_JS = """
() => {
    const direct = document.querySelector('#recaptcha-anchor');
    const frame = document.querySelector('iframe[title="reCAPTCHA"]');
    const framed = frame?.contentDocument?.querySelector('#recaptcha-anchor');
    (direct || framed)?.click();
}
"""

_HCAPTCHA_CHECKBOX_JS = """
() => {
    const direct = document.querySelector('#checkbox');
    const frame = document.querySelector(
        'iframe[title="Widget containing checkbox for '
        + 'hCaptcha security challenge"]'
    );
    const framed = frame?.contentDocument?.querySelector('#checkbox');
    (direct || framed)?.click();
}
"""

_TURNSTILE_CHECKBOX_JS = """
() => {
    const direct = document.querySelector(
        '.cf-turnstile input[type="checkbox"], '
        + '.ctp-checkbox-label, .cf-turnstile label'
    );
    const frame = document.querySelector(
        'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'
    );
    const framed = frame?.contentDocument?.querySelector(
        'input[type="checkbox"], .ctp-checkbox-label, label'
    );
    (direct || framed)?.click();
}
"""

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

_CHECKBOX_SCRIPTS = {
    ChallengeType.RECAPTCHA_V2: _RECAPTCHA_V2_CHECKBOX_JS,
    ChallengeType.HCAPTCHA: _HCAPTCHA_CHECKBOX_JS,
    ChallengeType.TURNSTILE: _TURNSTILE_CHECKBOX_JS,
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
            expression = _CHECKBOX_SCRIPTS[detection.challenge_type]
        await page.evaluate(expression)

    def __repr__(self) -> str:
        return 'OhMyCaptchaHandler(mode=deterministic_same_page)'
