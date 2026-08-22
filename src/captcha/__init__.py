"""Safe, same-page CAPTCHA detection and coordination contracts."""

from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.detector import detect_challenge
from src.captcha.handlers import ChallengeHandler, OhMyCaptchaHandler
from src.captcha.models import (
    ChallengeDetection,
    ChallengeResolution,
    ChallengeType,
)
from src.captcha.ohmycaptcha_adapter import OhMyCaptchaAdapter
from src.captcha.smartcaptcha import SmartCaptchaHandler, SmartCaptchaMode


__all__ = (
    'ChallengeCoordinator',
    'ChallengeDetection',
    'ChallengeHandler',
    'ChallengeResolution',
    'ChallengeType',
    'OhMyCaptchaAdapter',
    'OhMyCaptchaHandler',
    'SmartCaptchaHandler',
    'SmartCaptchaMode',
    'detect_challenge',
)
