"""Fail-closed same-page handling for Yandex SmartCaptcha."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
import json
import math
import re
import time
from typing import Any, Protocol, TypeVar

from src.browser.contracts import PageLike
from src.captcha.detector import detect_challenge
from src.captcha.models import (
    ChallengeDetection,
    ChallengeResolution,
    ChallengeType,
)


T = TypeVar('T')
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

_WIDGET_ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
_POLL_INTERVAL_SEC = 0.01
_FRICTIONLESS_SCRIPT = """
async () => {
    const api = window.smartCaptcha;
    const widgetId = __WIDGET_ID__;
    if (
        !api
        || typeof api.subscribe !== 'function'
        || typeof api.execute !== 'function'
    ) {
        return 'api_unavailable';
    }
    return await new Promise((resolve) => {
        const unsubscribers = [];
        let settled = false;
        let setupComplete = false;
        let successObserved = false;
        const finish = (status) => {
            if (settled) return;
            settled = true;
            for (const unsubscribe of unsubscribers) {
                try {
                    unsubscribe();
                } catch (_) {
                    // Listener cleanup must not expose provider errors.
                }
            }
            resolve(status);
        };
        const fail = (status) => finish(status);
        const succeed = () => {
            if (settled) return;
            successObserved = true;
            if (setupComplete) finish('success');
        };
        const subscribe = (event, callback) => {
            if (settled) return false;
            const unsubscribe = api.subscribe(
                widgetId,
                event,
                callback,
            );
            if (typeof unsubscribe === 'function') {
                if (settled) {
                    try {
                        unsubscribe();
                    } catch (_) {
                        // Listener cleanup must not expose provider errors.
                    }
                } else {
                    unsubscribers.push(unsubscribe);
                }
            }
            return !settled;
        };
        try {
            if (!subscribe(
                'challenge-visible',
                () => fail('challenge_visible'),
            )) return;
            if (!subscribe(
                'network-error',
                () => fail('network_error'),
            )) return;
            if (!subscribe(
                'javascript-error',
                () => fail('javascript_error'),
            )) return;
            if (!subscribe('success', () => succeed())) return;
            if (!subscribe(
                'token-expired',
                () => fail('token_expired'),
            )) return;
            api.execute(widgetId);
        } catch (_) {
            fail('javascript_error');
            return;
        }
        setupComplete = true;
        if (successObserved) finish('success');
    });
}
"""


class DeadlineLike(Protocol):
    """Absolute monotonic deadline owned by the marketplace operation."""

    expires_at: float


class SmartCaptchaMode(StrEnum):
    """Explicit SmartCaptcha automation modes."""

    DISABLED = 'disabled'
    FRICTIONLESS = 'frictionless'


def is_valid_widget_id(value: str) -> bool:
    """Return whether ``value`` is safe for the trusted widget contract."""
    return _WIDGET_ID_PATTERN.fullmatch(value) is not None


class SmartCaptchaHandler:
    """Observe official callbacks for one trusted existing widget."""

    __slots__ = (
        '_background_tasks',
        '_clock',
        '_mode',
        '_poll_interval_sec',
        '_sleep',
        '_widget_id',
    )

    def __init__(
        self,
        mode: SmartCaptchaMode,
        *,
        widget_id: str | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        poll_interval_sec: float = _POLL_INTERVAL_SEC,
    ) -> None:
        if not math.isfinite(poll_interval_sec) or poll_interval_sec <= 0:
            raise ValueError('poll_interval_sec must be finite and positive')
        self._mode = SmartCaptchaMode(mode)
        self._widget_id = (
            widget_id if widget_id and is_valid_widget_id(widget_id) else None
        )
        self._clock = clock
        self._sleep = sleep
        self._poll_interval_sec = poll_interval_sec
        self._background_tasks: set[asyncio.Future[Any]] = set()

    async def solve(
        self,
        page: PageLike,
        detection: ChallengeDetection,
        deadline: DeadlineLike,
    ) -> ChallengeResolution:
        """Attempt a configured frictionless widget on ``page`` only."""
        if (
            self._mode is not SmartCaptchaMode.FRICTIONLESS
            or self._widget_id is None
            or detection.challenge_type is not ChallengeType.UNKNOWN
            or detection.is_interactive
        ):
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        expression = _FRICTIONLESS_SCRIPT.replace(
            '__WIDGET_ID__',
            json.dumps(self._widget_id),
        )
        try:
            status = await self._bounded(
                lambda: page.evaluate(expression),
                page,
                deadline,
            )
        except asyncio.TimeoutError:
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        except asyncio.CancelledError:
            raise
        except Exception:
            return ChallengeResolution.CHALLENGE_UNSOLVABLE

        if status != 'success':
            return ChallengeResolution.CHALLENGE_UNSOLVABLE
        return await self._wait_until_challenge_is_gone(page, deadline)

    def __repr__(self) -> str:
        return (
            'SmartCaptchaHandler('
            f'mode={self._mode.value}, policy=same_page)'
        )

    async def _wait_until_challenge_is_gone(
        self,
        page: PageLike,
        deadline: DeadlineLike,
    ) -> ChallengeResolution:
        while True:
            try:
                detection = await self._bounded(
                    lambda: detect_challenge(page),
                    page,
                    deadline,
                )
            except asyncio.TimeoutError:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE
            except asyncio.CancelledError:
                raise
            except Exception:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE

            if detection.challenge_type is ChallengeType.NONE:
                return ChallengeResolution.SOLVED
            if (
                detection.challenge_type is not ChallengeType.UNKNOWN
                or detection.is_interactive
            ):
                return ChallengeResolution.CHALLENGE_UNSOLVABLE

            remaining = deadline.expires_at - self._clock()
            if not math.isfinite(remaining) or remaining <= 0:
                self._schedule_page_close(page)
                return ChallengeResolution.CHALLENGE_UNSOLVABLE
            try:
                await self._bounded(
                    lambda: self._sleep(
                        min(self._poll_interval_sec, remaining)
                    ),
                    page,
                    deadline,
                )
            except asyncio.TimeoutError:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE
            except asyncio.CancelledError:
                raise
            except Exception:
                return ChallengeResolution.CHALLENGE_UNSOLVABLE

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
