"""Secret-free CAPTCHA domain values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChallengeType(StrEnum):
    """Challenge families recognized by the controlled detector."""

    NONE = 'none'
    RECAPTCHA_V2 = 'recaptcha_v2'
    RECAPTCHA_V3 = 'recaptcha_v3'
    HCAPTCHA = 'hcaptcha'
    TURNSTILE = 'turnstile'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class ChallengeDetection:
    """A safe detector result that never retains page content or tokens."""

    challenge_type: ChallengeType
    is_interactive: bool = False


class ChallengeResolution(StrEnum):
    """Final same-page challenge outcome."""

    NO_CHALLENGE = 'no_challenge'
    SOLVED = 'solved'
    CHALLENGE_UNSOLVABLE = 'challenge_unsolvable'
