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
        self.close_calls = 0
        self.closed = False
        self.late_actions = 0

    async def content(self) -> str:
        self.content_calls += 1
        return self.html

    async def evaluate(self, expression: str) -> None:
        self.evaluated.append(expression)
        if self.clear_on_evaluate:
            self.html = load_fixture('challenges/clean.html')

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class FrameCheckbox:
    def __init__(self, page: FrameOwnedPage) -> None:
        self.page = page
        self.click_timeouts: list[float] = []

    async def click(self, *, timeout: float) -> None:
        self.click_timeouts.append(timeout)
        self.page.html = load_fixture('challenges/clean.html')


class OwnedFrame:
    def __init__(self, page: FrameOwnedPage, url: str) -> None:
        self.owner = page
        self._url = url
        self.checkbox = FrameCheckbox(page)
        self.selectors: list[str] = []

    @property
    def url(self) -> str:
        return self._url

    def locator(self, selector: str) -> FrameCheckbox:
        self.selectors.append(selector)
        return self.checkbox


class MainFrameLocator:
    def __init__(self, page: FrameOwnedPage) -> None:
        self.page = page

    @property
    def content_frame(self) -> MainFrameLocator:
        return self

    def locator(self, selector: str) -> MainFrameLocator:
        return self

    async def get_attribute(
        self,
        name: str,
        *,
        timeout: float,
    ) -> str | None:
        return None

    async def click(self, *, timeout: float) -> None:
        self.page.main_frame_clicks += 1


class IframeElementLocator:
    def __init__(self, page: TitleOwnedPage, frame: OwnedFrame) -> None:
        self._page = page
        self._frame = frame

    @property
    def content_frame(self) -> OwnedFrame:
        return self._frame

    async def get_attribute(
        self,
        name: str,
        *,
        timeout: float,
    ) -> str | None:
        self._page.attribute_timeouts.append(timeout)
        if name == 'src':
            return self._page.declared_src
        return None


class FrameOwnedPage(FakePage):
    def __init__(self, html: str, frame_url: str) -> None:
        super().__init__(html)
        self.frame = OwnedFrame(self, frame_url)
        self._frames = (self.frame,)
        self.main_frame_clicks = 0
        self.evaluate_calls = 0

    @property
    def frames(self) -> tuple[OwnedFrame, ...]:
        return self._frames

    def locator(self, selector: str) -> MainFrameLocator:
        return MainFrameLocator(self)

    async def evaluate(self, expression: str) -> None:
        self.evaluate_calls += 1


class TitleOwnedPage(FrameOwnedPage):
    def __init__(
        self,
        html: str,
        frame_url: str,
        *,
        declared_src: str | None = None,
        expose_frame: bool = False,
    ) -> None:
        super().__init__(html, frame_url)
        self.declared_src = declared_src or frame_url
        self._frames = (self.frame,) if expose_frame else ()
        self.attribute_timeouts: list[float] = []

    def locator(self, selector: str) -> IframeElementLocator:
        return IframeElementLocator(self, self.frame)


class LateFramePage(TitleOwnedPage):
    def __init__(
        self,
        html: str,
        frame_url: str,
        *,
        available_after_read: int,
    ) -> None:
        super().__init__(html, frame_url)
        self.available_after_read = available_after_read
        self.frame_reads = 0

    @property
    def frames(self) -> tuple[OwnedFrame, ...]:
        self.frame_reads += 1
        if self.frame_reads >= self.available_after_read:
            return (self.frame,)
        return ()


class UrlChangingFrame(OwnedFrame):
    def __init__(
        self,
        page: FrameOwnedPage,
        urls: tuple[str, ...],
    ) -> None:
        super().__init__(page, urls[-1])
        self._urls = urls
        self.url_reads = 0

    @property
    def url(self) -> str:
        index = min(self.url_reads, len(self._urls) - 1)
        self.url_reads += 1
        return self._urls[index]


class UrlChangingFramePage(FrameOwnedPage):
    def __init__(
        self,
        html: str,
        frame_urls: tuple[str, ...],
    ) -> None:
        super().__init__(html, frame_urls[0])
        self.frame = UrlChangingFrame(self, frame_urls)
        self._frames = (self.frame,)


class MissingCheckbox(FrameCheckbox):
    async def click(self, *, timeout: float) -> None:
        self.click_timeouts.append(timeout)
        raise RuntimeError('provider checkbox is unavailable')


class MissingFrame(OwnedFrame):
    def __init__(self, page: FrameOwnedPage, url: str) -> None:
        super().__init__(page, url)
        self.checkbox = MissingCheckbox(page)


class MissingFramePage(TitleOwnedPage):
    def __init__(self, html: str, frame_url: str) -> None:
        super().__init__(html, frame_url)
        self.frame = MissingFrame(self, frame_url)
        self._frames = (self.frame,)


class DelayedChallengePage(FakePage):
    def __init__(
        self,
        html: str,
        *,
        clean_after_call: int | None = None,
        changed_html: str | None = None,
    ) -> None:
        super().__init__(html)
        self.clean_after_call = clean_after_call
        self.changed_html = changed_html

    async def content(self) -> str:
        self.content_calls += 1
        if (
            self.clean_after_call is not None
            and self.content_calls >= self.clean_after_call
        ):
            return load_fixture('challenges/clean.html')
        if self.changed_html is not None and self.content_calls > 1:
            return self.changed_html
        return self.html


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
        *,
        timeout_ms: float,
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
        *,
        timeout_ms: float,
    ) -> None:
        self.page = page
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


class CancellationSuppressingHandler(RecordingHandler):
    def __init__(self) -> None:
        super().__init__(clear_challenge=False)
        self.release = asyncio.Event()
        self.suppressed = asyncio.Event()

    async def handle(
        self,
        page: FakePage,
        detection: ChallengeDetection,
        *,
        timeout_ms: float,
    ) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.suppressed.set()
            if not page.closed:
                page.late_actions += 1
            await self.release.wait()
            raise RuntimeError('late handler failure')


class ChallengeCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkbox_is_clicked_inside_owned_provider_frame(
        self,
    ) -> None:
        cases = (
            (
                '<iframe src="https://www.google.com/recaptcha/api2/'
                'anchor"></iframe>',
                'https://www.google.com/recaptcha/api2/anchor',
                '#recaptcha-anchor',
            ),
            (
                '<iframe src="https://newassets.hcaptcha.com/captcha/'
                'v1/static/hcaptcha.html#frame=checkbox"></iframe>',
                (
                    'https://newassets.hcaptcha.com/captcha/v1/static/'
                    'hcaptcha.html#frame=checkbox'
                ),
                '#checkbox',
            ),
            (
                '<iframe src="https://challenges.cloudflare.com/'
                'turnstile/v0/widget"></iframe>',
                (
                    'https://challenges.cloudflare.com/turnstile/'
                    'v0/widget'
                ),
                (
                    'input[type="checkbox"], [role="checkbox"], '
                    '.ctp-checkbox-label'
                ),
            ),
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )

        for html, frame_url, expected_selector in cases:
            with self.subTest(frame_url=frame_url):
                page = FrameOwnedPage(html, frame_url)
                coordinator = ChallengeCoordinator(
                    (handler,),
                    clock=lambda: 0.0,
                )

                resolution = await coordinator.resolve(
                    page,
                    deadline=FakeDeadline(5.0),
                )

                self.assertIs(page, page.frame.owner)
                self.assertEqual(1, len(page.frame.checkbox.click_timeouts))
                self.assertLessEqual(
                    page.frame.checkbox.click_timeouts[0],
                    5000,
                )
                self.assertEqual(
                    expected_selector,
                    page.frame.selectors[0],
                )
                self.assertEqual(0, page.main_frame_clicks)
                self.assertEqual(0, page.evaluate_calls)
                self.assertIs(ChallengeResolution.SOLVED, resolution)

    async def test_polls_until_actual_provider_frame_is_available(
        self,
    ) -> None:
        page = LateFramePage(
            '<iframe title="reCAPTCHA" '
            'src="https://www.google.com/recaptcha/api2/anchor">'
            '</iframe>',
            'https://www.google.com/recaptcha/api2/anchor',
            available_after_read=3,
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )
        coordinator = ChallengeCoordinator((handler,))

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(time.monotonic() + 0.2),
        )

        self.assertEqual(1, len(page.frame.checkbox.click_timeouts))
        self.assertGreaterEqual(page.frame_reads, 3)
        self.assertEqual([], page.attribute_timeouts)
        self.assertEqual(0, page.main_frame_clicks)
        self.assertIs(ChallengeResolution.SOLVED, resolution)

    async def test_title_does_not_authorize_untrusted_frame(self) -> None:
        trusted_src = (
            'https://challenges.cloudflare.com/turnstile/v0/widget'
        )
        untrusted_frames = (
            (trusted_src, 'https://evil.invalid/turnstile/widget'),
            (
                'https://evil.invalid/turnstile/widget',
                'https://evil.invalid/turnstile/widget',
            ),
            (
                'https://challenges.cloudflare.com/account'
                '?next=/turnstile/widget',
                'https://challenges.cloudflare.com/account'
                '?next=/turnstile/widget',
            ),
            (
                'http://challenges.cloudflare.com/turnstile/v0/widget',
                'http://challenges.cloudflare.com/turnstile/v0/widget',
            ),
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )

        for declared_src, frame_url in untrusted_frames:
            with self.subTest(frame_url=frame_url):
                page = TitleOwnedPage(
                    '<iframe title="Widget containing a Cloudflare '
                    f'security challenge" src="{declared_src}"></iframe>',
                    frame_url,
                    declared_src=declared_src,
                    expose_frame=True,
                )
                coordinator = ChallengeCoordinator((handler,))

                with self.assertLogs(
                    'src.captcha.coordinator',
                    level='WARNING',
                ):
                    resolution = await coordinator.resolve(
                        page,
                        deadline=FakeDeadline(time.monotonic() + 0.03),
                    )

                self.assertEqual([], page.frame.checkbox.click_timeouts)
                self.assertEqual([], page.attribute_timeouts)
                self.assertIs(
                    ChallengeResolution.CHALLENGE_UNSOLVABLE,
                    resolution,
                )

    async def test_revalidates_current_frame_url_before_click(self) -> None:
        page = UrlChangingFramePage(
            '<iframe src="https://challenges.cloudflare.com/'
            'turnstile/v0/widget"></iframe>',
            (
                'https://challenges.cloudflare.com/turnstile/v0/widget',
                'https://evil.invalid/turnstile/widget',
            ),
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )
        coordinator = ChallengeCoordinator((handler,))

        with self.assertLogs(
            'src.captcha.coordinator',
            level='WARNING',
        ):
            resolution = await coordinator.resolve(
                page,
                deadline=FakeDeadline(time.monotonic() + 0.05),
            )

        self.assertGreaterEqual(page.frame.url_reads, 2)
        self.assertEqual([], page.frame.checkbox.click_timeouts)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_unavailable_iframe_checkbox_is_not_a_silent_noop(
        self,
    ) -> None:
        page = MissingFramePage(
            '<iframe title="reCAPTCHA" '
            'src="https://www.google.com/recaptcha/api2/anchor">'
            '</iframe>',
            'https://www.google.com/recaptcha/api2/anchor',
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        with self.assertLogs(
            'src.captcha.coordinator',
            level='WARNING',
        ) as logs:
            resolution = await coordinator.resolve(
                page,
                deadline=FakeDeadline(5.0),
            )

        self.assertEqual(1, len(page.frame.checkbox.click_timeouts))
        self.assertIn('challenge_handler_failed', '\n'.join(logs.output))
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

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
        coordinator = ChallengeCoordinator((handler,))

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(time.monotonic() + 0.04),
        )

        self.assertGreaterEqual(page.content_calls, 3)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_polls_until_challenge_disappears(self) -> None:
        page = DelayedChallengePage(
            load_fixture('challenges/recaptcha-v2.html'),
            clean_after_call=4,
        )
        handler = RecordingHandler(clear_challenge=False)
        coordinator = ChallengeCoordinator((handler,))

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(time.monotonic() + 0.2),
        )

        self.assertGreaterEqual(page.content_calls, 4)
        self.assertIs(ChallengeResolution.SOLVED, resolution)

    async def test_polls_remaining_challenge_until_deadline(self) -> None:
        page = DelayedChallengePage(
            load_fixture('challenges/recaptcha-v2.html')
        )
        handler = RecordingHandler(clear_challenge=False)
        coordinator = ChallengeCoordinator((handler,))

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(time.monotonic() + 0.04),
        )

        self.assertGreaterEqual(page.content_calls, 3)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_polls_changed_challenge_until_deadline(self) -> None:
        page = DelayedChallengePage(
            load_fixture('challenges/recaptcha-v2.html'),
            changed_html=load_fixture('challenges/hcaptcha.html'),
        )
        handler = RecordingHandler(clear_challenge=False)
        coordinator = ChallengeCoordinator((handler,))

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(time.monotonic() + 0.04),
        )

        self.assertGreaterEqual(page.content_calls, 3)
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
        await asyncio.sleep(0)

        self.assertLess(elapsed, 0.5)
        self.assertTrue(handler.cancelled)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_hard_deadline_closes_page_when_cancel_is_suppressed(
        self,
    ) -> None:
        page = FakePage(load_fixture('challenges/recaptcha-v2.html'))
        handler = CancellationSuppressingHandler()
        coordinator = ChallengeCoordinator((handler,))
        loop = asyncio.get_running_loop()
        unhandled: list[dict[str, object]] = []
        previous_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda current_loop, context: unhandled.append(context)
        )

        async def release_handler() -> None:
            await asyncio.sleep(0.08)
            handler.release.set()

        release_task = asyncio.create_task(release_handler())
        try:
            started_at = time.monotonic()
            resolution = await coordinator.resolve(
                page,
                deadline=FakeDeadline(time.monotonic() + 0.01),
            )
            elapsed = time.monotonic() - started_at
            await release_task
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_exception_handler)

        self.assertLess(elapsed, 0.05)
        self.assertTrue(handler.suppressed.is_set())
        self.assertGreaterEqual(page.close_calls, 1)
        self.assertEqual(0, page.late_actions)
        self.assertEqual([], unhandled)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_reviewed_handler_acts_on_the_injected_page(self) -> None:
        page = FrameOwnedPage(
            '<iframe src="https://www.google.com/recaptcha/'
            'api2/anchor"></iframe>',
            'https://www.google.com/recaptcha/api2/anchor',
        )
        handler = OhMyCaptchaHandler(
            OhMyCaptchaAdapter(vendor_root=VENDOR_ROOT)
        )
        coordinator = ChallengeCoordinator((handler,), clock=lambda: 0.0)

        resolution = await coordinator.resolve(
            page,
            deadline=FakeDeadline(5.0),
        )

        self.assertEqual(1, len(page.frame.checkbox.click_timeouts))
        self.assertIn('recaptcha-anchor', page.frame.selectors[0])
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
