"""Deterministic, content-discarding challenge detection."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from src.browser.contracts import PageLike
from src.captcha.models import ChallengeDetection, ChallengeType


_PRIORITY = (
    ChallengeType.RECAPTCHA_V2,
    ChallengeType.RECAPTCHA_V3,
    ChallengeType.HCAPTCHA,
    ChallengeType.TURNSTILE,
)

_INTERACTIVE_KINDS = frozenset(
    ('image', 'audio', 'slider', 'visual', 'model')
)

_VOID_ELEMENTS = frozenset(
    (
        'area',
        'base',
        'br',
        'col',
        'embed',
        'hr',
        'img',
        'input',
        'link',
        'meta',
        'param',
        'source',
        'track',
        'wbr',
    )
)


async def detect_challenge(page: PageLike) -> ChallengeDetection:
    """Classify active challenge markup without retaining page content."""
    parser = _ChallengeMarkupParser()
    parser.feed(await page.content())
    parser.close()

    for challenge_type in _PRIORITY:
        if challenge_type in parser.active:
            return ChallengeDetection(
                challenge_type=challenge_type,
                is_interactive=challenge_type in parser.interactive,
            )
    if parser.unknown:
        return ChallengeDetection(
            challenge_type=ChallengeType.UNKNOWN,
            is_interactive=parser.unknown_interactive,
        )
    return ChallengeDetection(challenge_type=ChallengeType.NONE)


class _ChallengeMarkupParser(HTMLParser):
    """Collect structural challenge facts and discard all page text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active: set[ChallengeType] = set()
        self.interactive: set[ChallengeType] = set()
        self.unknown = False
        self.unknown_interactive = False
        self._elements: list[
            tuple[str, frozenset[ChallengeType], bool, bool]
        ] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        inherited_providers = (
            self._elements[-1][1] if self._elements else frozenset()
        )
        inherited_inert = self._elements[-1][2] if self._elements else False
        inherited_unknown = (
            self._elements[-1][3] if self._elements else False
        )
        inert = inherited_inert or normalized_tag in (
            'script',
            'style',
            'template',
        )
        attributes = {
            name.lower(): (value or '').lower()
            for name, value in attrs
        }

        providers: set[ChallengeType] = set()
        if not inert:
            providers = _active_providers(normalized_tag, attributes)
            self.active.update(providers)
            for challenge_type in providers:
                if _is_interactive_element(
                    normalized_tag,
                    attributes,
                    challenge_type,
                ):
                    self.interactive.add(challenge_type)

            if inherited_providers and _has_interactive_marker(
                normalized_tag,
                attributes,
            ):
                self.interactive.update(inherited_providers)

            current_unknown = _is_unknown_challenge_element(attributes)
            if current_unknown:
                self.unknown = True
            if (
                (current_unknown or inherited_unknown)
                and _has_interactive_marker(normalized_tag, attributes)
            ):
                self.unknown_interactive = True
        else:
            current_unknown = False

        if normalized_tag not in _VOID_ELEMENTS:
            current_providers = inherited_providers | providers
            self._elements.append(
                (
                    normalized_tag,
                    frozenset(current_providers),
                    inert,
                    inherited_unknown or current_unknown,
                )
            )

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] == normalized_tag:
                del self._elements[index:]
                return


def _active_providers(
    tag: str,
    attrs: dict[str, str],
) -> set[ChallengeType]:
    providers: set[ChallengeType] = set()
    class_tokens = _attribute_tokens(attrs, 'class')
    identifier = attrs.get('id', '')
    name = attrs.get('name', '')
    title = attrs.get('title', '')
    recaptcha_identity = class_tokens | {identifier}
    recaptcha_v3_owned = (
        bool(
            recaptcha_identity & {'grecaptcha-v3', 'recaptcha-v3'}
        )
        or (
            'g-recaptcha' in class_tokens
            and attrs.get('data-size', '') == 'invisible'
        )
    )

    if (
        ('g-recaptcha' in class_tokens and not recaptcha_v3_owned)
        or identifier in ('g-recaptcha-response', 'recaptcha-anchor')
        or name == 'g-recaptcha-response'
        or (
            tag == 'iframe'
            and (
                title == 'recaptcha'
                or (
                    'recaptcha' in title
                    and 'challenge' in _text_tokens(title)
                )
                or _matches_recaptcha_frame(attrs.get('src', ''))
            )
        )
    ):
        providers.add(ChallengeType.RECAPTCHA_V2)

    if (
        'data-sitekey' in attrs
        and recaptcha_v3_owned
    ):
        providers.add(ChallengeType.RECAPTCHA_V3)

    if (
        'h-captcha' in class_tokens
        or identifier == 'h-captcha-response'
        or name == 'h-captcha-response'
        or (
            tag == 'iframe'
            and (
                'hcaptcha' in title
                or _matches_hcaptcha_frame(attrs.get('src', ''))
            )
        )
    ):
        providers.add(ChallengeType.HCAPTCHA)

    if (
        'cf-turnstile' in class_tokens
        or identifier == 'cf-turnstile-response'
        or name == 'cf-turnstile-response'
        or (
            tag == 'iframe'
            and (
                'cloudflare security challenge' in title
                or _matches_turnstile_frame(attrs.get('src', ''))
            )
        )
    ):
        providers.add(ChallengeType.TURNSTILE)
    return providers


def _is_interactive_element(
    tag: str,
    attrs: dict[str, str],
    challenge_type: ChallengeType,
) -> bool:
    if _has_interactive_marker(tag, attrs):
        return True
    if tag != 'iframe':
        return False

    title = attrs.get('title', '')
    source = attrs.get('src', '')
    if challenge_type is ChallengeType.RECAPTCHA_V2:
        title_tokens = _text_tokens(title)
        return (
            'challenge' in title_tokens
            or bool(title_tokens & _INTERACTIVE_KINDS)
            or '/bframe' in source
        )
    if challenge_type is ChallengeType.HCAPTCHA:
        return (
            bool(_text_tokens(title) & _INTERACTIVE_KINDS)
            or 'main content of the hcaptcha challenge' in title
            or 'frame=challenge' in source
        )
    return False


def _has_interactive_marker(
    tag: str,
    attrs: dict[str, str],
) -> bool:
    challenge_kind = attrs.get('data-challenge-type', '')
    class_tokens = _attribute_tokens(attrs, 'class')
    identifier_tokens = frozenset(
        token for token in attrs.get('id', '').replace('_', '-').split('-')
    )
    if challenge_kind in _INTERACTIVE_KINDS or (
        (class_tokens | identifier_tokens) & _INTERACTIVE_KINDS
    ):
        return True
    if tag != 'iframe':
        return False
    return bool(_text_tokens(attrs.get('title', '')) & _INTERACTIVE_KINDS)


def _is_unknown_challenge_element(attrs: dict[str, str]) -> bool:
    values = ' '.join(
        attrs.get(name, '') for name in ('id', 'class', 'name')
    )
    normalized = values.replace('_', '-').lower()
    return 'captcha' in normalized or 'human-verification' in normalized


def _attribute_tokens(
    attrs: dict[str, str],
    name: str,
) -> frozenset[str]:
    return frozenset(attrs.get(name, '').split())


def _text_tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r'[a-z0-9]+', value))


def _matches_recaptcha_frame(url: str) -> bool:
    parsed = urlsplit(url)
    if not _host_matches(parsed.hostname, ('google.com', 'recaptcha.net')):
        return False
    target = f'{parsed.path}?{parsed.query}#{parsed.fragment}'.lower()
    return '/recaptcha/' in target and (
        'anchor' in target or 'bframe' in target
    )


def _matches_hcaptcha_frame(url: str) -> bool:
    parsed = urlsplit(url)
    return _host_matches(parsed.hostname, ('hcaptcha.com',)) and (
        'hcaptcha' in parsed.path.lower()
        or 'frame=' in parsed.fragment.lower()
    )


def _matches_turnstile_frame(url: str) -> bool:
    parsed = urlsplit(url)
    return _host_matches(
        parsed.hostname,
        ('challenges.cloudflare.com',),
    ) and '/turnstile/' in parsed.path.lower()


def _host_matches(
    hostname: str | None,
    allowed_hosts: tuple[str, ...],
) -> bool:
    normalized = (hostname or '').lower()
    return any(
        normalized == host or normalized.endswith(f'.{host}')
        for host in allowed_hosts
    )
