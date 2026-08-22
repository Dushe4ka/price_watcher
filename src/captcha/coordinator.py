"""Bounded same-page challenge coordination."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, TypeVar

from src.browser.contracts import PageLike
from src.captcha.detector import detect_challenge
from src.captcha.handlers import ChallengeHandler
from src.captcha.models import ChallengeResolution, ChallengeType


log = logging.getLogger(__name__)

T = TypeVar('T')
Clock = Callable[[], float]


class DeadlineLike(Protocol):
    """Absolute monotonic deadline accepted from the operation owner."""

    expires_at: float


class ChallengeCoordinator:
    """Attempt a safe handler and trust only a clean re-detection."""

    __slots__ = ('_clock', '_handlers')

    def __init__(
        self,
        handlers: Sequence[ChallengeHandler],
        clock: Clock = time.monotonic,
    ) -> None:
        self._handlers = tuple(handlers)
        self._clock = clock

    async def resolve(
        self,
        page: PageLike,
        *,
        deadline: DeadlineLike,
    ) -> ChallengeResolution:
        """Resolve only deterministic challenges on the supplied page."""
        try:
            detection = await self._bounded(
                lambda: detect_challenge(page),
                deadline,
            )
        except asyncio.TimeoutError:
            self._log_failure('challenge_deadline_exceeded')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        except Exception:
            self._log_failure('challenge_detection_failed')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        if detection.challenge_type is ChallengeType.NONE:
            return ChallengeResolution.NO_CHALLENGE
        if (
            detection.challenge_type is ChallengeType.UNKNOWN
            or detection.is_interactive
        ):
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        try:
            handler = next(
                candidate
                for candidate in self._handlers
                if candidate.supports(detection)
            )
        except StopIteration:
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        except Exception:
            self._log_failure('challenge_handler_selection_failed')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        try:
            await self._bounded(
                lambda: handler.handle(page, detection),
                deadline,
            )
        except asyncio.TimeoutError:
            self._log_failure('challenge_deadline_exceeded')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        except Exception:
            self._log_failure('challenge_handler_failed')

        try:
            redetection = await self._bounded(
                lambda: detect_challenge(page),
                deadline,
            )
        except asyncio.TimeoutError:
            self._log_failure('challenge_deadline_exceeded')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        except Exception:
            self._log_failure('challenge_detection_failed')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        if redetection.challenge_type is ChallengeType.NONE:
            return ChallengeResolution.SOLVED
        return ChallengeResolution.CHALLENGE_UNSOLVABLE

    def __repr__(self) -> str:
        return 'ChallengeCoordinator(policy=bounded_same_page)'

    async def _bounded(
        self,
        operation: Callable[[], Awaitable[T]],
        deadline: DeadlineLike,
    ) -> T:
        remaining = deadline.expires_at - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(operation(), timeout=remaining)

    @staticmethod
    def _log_failure(error_code: str) -> None:
        log.warning('CAPTCHA operation failed (%s)', error_code)
