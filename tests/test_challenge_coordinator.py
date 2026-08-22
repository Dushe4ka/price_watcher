from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.handlers import OhMyCaptchaHandler
from src.captcha.models import (
    ChallengeDetection,
    ChallengeResolution,
)
from src.captcha.ohmycaptcha_adapter import OhMyCaptchaAdapter


FIXTURES = Path(__file__).parent / 'fixtures'
VENDOR_ROOT = Path(__file__).parents[1] / 'vendor' / 'ohmycaptcha'


def load_fixture(path: str) -> str:
    return (FIXTURES / path).read_text(encoding='utf-8')


@dataclass(frozen=True)
class FakeDeadline:
    expires_at: float


class FakePage:
    def __init__(self, html: str, clear_on_evaluate: bool = False) -> None:
        self.html = html
        self.clear_on_evaluate = clear_on_evaluate
        self.content_calls = 0
        self.evaluated: list[str] = []

    async def content(self) -> str:
        self.content_calls += 1
        return self.html

    async def evaluate(self, expression: str) -> None:
        self.evaluated.append(expression)
        if self.clear_on_evaluate:
            self.html = load_fixture('challenges/clean.html')


class RecordingHandler:
    def __init__(self, clear_challenge: bool = True) -> None:
        self.clear_challenge = clear_challenge
        self.page: FakePage | None = None
        self.calls = 0

    def supports(self, detection: ChallengeDetection) -> bool:
        return True

    async def handle(
        self,
        page: FakePage,
        detection: ChallengeDetection,
    ) -> None:
        self.calls += 1
        self.page = page
        if self.clear_challenge:
            page.html = load_fixture('challenges/clean.html')


class HangingHandler(RecordingHandler):
    def __init__(self) -> None:
        super().__init__(clear_challenge=False)
        self.cancelled = False

    async def handle(
        self,
        page: FakePage,
        detection: ChallengeDetection,
    ) -> None:
        self.page = page
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class ChallengeCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_coordinator_uses_the_leased_page(self) -> None:
        page = FakePage(load_fixture('challenges/recaptcha-v2.html'))
        handler = RecordingHandler()
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(5.0),
        )

        self.assertIs(page, handler.page)
        self.assertIs(ChallengeResolution.SOLVED, resolution)

    async def test_re_detects_and_rejects_unchanged_challenge(self) -> None:
        page = FakePage(load_fixture('challenges/recaptcha-v2.html'))
        handler = RecordingHandler(clear_challenge=False)
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(5.0),
        )

        self.assertEqual(2, page.content_calls)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_unknown_and_interactive_challenges_are_not_handled(
        self,
    ) -> None:
        cases = (
            load_fixture('challenges/unknown.html'),
            (
                '<iframe title="recaptcha challenge"></iframe>'
                '<div class="g-recaptcha"></div>'
            ),
        )

        for html in cases:
            with self.subTest(html_case=len(html)):
                page = FakePage(html)
                handler = RecordingHandler()
                coordinator = ChallengeCoordinator(
                    (handler,),
                    clock=lambda: 0.0,
                )

                resolution = await coordinator.resolve(
                    page,
                    deadline=FakeDeadline(5.0),
                )

                self.assertEqual(0, handler.calls)
                self.assertIs(
                    ChallengeResolution.CHALLENGE_UNSOLVABLE,
                    resolution,
                )

    async def test_clean_page_needs_no_handler(self) -> None:
        page = FakePage(load_fixture('challenges/clean.html'))
        handler = RecordingHandler()
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(5.0),
        )

        self.assertEqual(0, handler.calls)
        self.assertIs(ChallengeResolution.NO_CHALLENGE, resolution)

    async def test_handler_is_cancelled_at_mandatory_deadline(
        self,
    ) -> None:
        page = FakePage(load_fixture('challenges/recaptcha-v2.html'))
        handler = HangingHandler()
        coordinator = ChallengeCoordinator((handler,))
        deadline = FakeDeadline(time.monotonic() + 0.01)

        started_at = time.monotonic()
        resolution = await coordinator.resolve(page, deadline=deadline)
        elapsed = time.monotonic() - started_at

        self.assertLess(elapsed, 0.5)
        self.assertTrue(handler.cancelled)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_reviewed_handler_acts_on_the_injected_page(self) -> None:
        page = FakePage(
            load_fixture('challenges/recaptcha-v2.html'),
            clear_on_evaluate=True,
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(5.0),
        )

        self.assertEqual(1, len(page.evaluated))
        self.assertIn('recaptcha-anchor', page.evaluated[0])
        self.assertIs(ChallengeResolution.SOLVED, resolution)

    async def test_handler_refuses_unavailable_vendor_contract(self) -> None:
        page = FakePage(
            load_fixture('challenges/recaptcha-v2.html'),
            clear_on_evaluate=True,
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(
                vendor_root=Path('/unavailable-vendor-snapshot')
            )
        )
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        with self.assertLogs(
            'src.captcha.coordinator',
            level='WARNING',
        ):
            resolution = await coordinator.resolve(
                page,
                deadline=FakeDeadline(5.0),
            )

        self.assertEqual([], page.evaluated)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )


if __name__ == '__main__':
    unittest.main()
