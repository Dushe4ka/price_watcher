"""Bounded same-page challenge coordination."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, TypeVar

from src.browser.contracts import PageLike
from src.captcha.detector import detect_challenge
from src.captcha.handlers import ChallengeHandler
from src.captcha.models import (
    ChallengeDetection,
    ChallengeResolution,
    ChallengeType,
)


log = logging.getLogger(__name__)

T = TypeVar('T')
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class DeadlineLike(Protocol):
    """Absolute monotonic deadline accepted from the operation owner."""

    expires_at: float


class SmartCaptchaResolver(Protocol):
    """Optional resolver for an explicitly configured unknown challenge."""

    async def solve(
        self,
        page: PageLike,
        detection: ChallengeDetection,
        deadline: DeadlineLike,
    ) -> ChallengeResolution:
        """Resolve only a trusted existing SmartCaptcha widget."""


class ChallengeCoordinator:
    """Attempt a safe handler and trust only a clean re-detection."""

    __slots__ = (
        '_background_tasks',
        '_clock',
        '_handlers',
        '_poll_interval_sec',
        '_sleep',
        '_smartcaptcha_handler',
    )

    def __init__(
        self,
        handlers: Sequence[ChallengeHandler],
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        poll_interval_sec: float = 0.01,
        smartcaptcha_handler: SmartCaptchaResolver | None = None,
    ) -> None:
        if not math.isfinite(poll_interval_sec) or poll_interval_sec <= 0:
            raise ValueError('poll_interval_sec must be finite and positive')
        self._handlers = tuple(handlers)
        self._clock = clock
        self._sleep = sleep
        self._poll_interval_sec = poll_interval_sec
        self._smartcaptcha_handler = smartcaptcha_handler
        self._background_tasks: set[asyncio.Future[Any]] = set()

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
                page,
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
        if detection.is_interactive:
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        if detection.challenge_type is ChallengeType.UNKNOWN:
            if self._smartcaptcha_handler is None:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE
            try:
                return await self._smartcaptcha_handler.solve(
                    page,
                    detection,
                    deadline,
                )
            except Exception:
                self._log_failure('challenge_handler_failed')
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
                lambda: handler.handle(
                    page,
                    detection,
                    timeout_ms=self._remaining_ms(deadline),
                ),
                page,
                deadline,
            )
        except asyncio.TimeoutError:
            self._log_failure('challenge_deadline_exceeded')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        except Exception:
            self._log_failure('challenge_handler_failed')
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        while True:
            try:
                redetection = await self._bounded(
                    lambda: detect_challenge(page),
                    page,
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

            remaining = deadline.expires_at - self._clock()
            if not math.isfinite(remaining) or remaining <= 0:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE
            delay = min(self._poll_interval_sec, remaining)
            try:
                await self._bounded(
                    lambda: self._sleep(delay),
                    page,
                    deadline,
                )
            except asyncio.TimeoutError:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE
            except Exception:
                self._log_failure('challenge_poll_failed')
                return ChallengeResolution.CHALLENGE_UNSOLVABLE

    def __repr__(self) -> str:
        return 'ChallengeCoordinator(policy=bounded_same_page)'

    async def _bounded(
        self,
        operation: Callable[[], Awaitable[T]],
        page: PageLike,
        deadline: DeadlineLike,
    ) -> T:
        remaining = deadline.expires_at - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            self._schedule_page_close(page)
            raise asyncio.TimeoutError
        task = asyncio.ensure_future(operation())
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
        except BaseException:
            self._schedule_page_close(page)
            self._track_background_task(task)
            task.cancel()
            raise
        if task in done:
            return task.result()
        self._schedule_page_close(page)
        self._track_background_task(task)
        task.cancel()
        raise asyncio.TimeoutError

    def _remaining_ms(self, deadline: DeadlineLike) -> float:
        remaining = deadline.expires_at - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise asyncio.TimeoutError
        return remaining * 1000

    def _schedule_page_close(self, page: PageLike) -> None:
        try:
            close_task = asyncio.ensure_future(page.close())
        except Exception:
            return
        self._track_background_task(close_task)

    def _track_background_task(
        self,
        task: asyncio.Future[Any],
    ) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._consume_background_task)

    def _consume_background_task(
        self,
        task: asyncio.Future[Any],
    ) -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except BaseException:
            pass

    @staticmethod
    def _log_failure(error_code: str) -> None:
        log.warning('CAPTCHA operation failed (%s)', error_code)
