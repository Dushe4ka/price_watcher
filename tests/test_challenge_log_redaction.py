from __future__ import annotations

import unittest
from dataclasses import dataclass

from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.models import ChallengeDetection, ChallengeResolution


SENTINELS = (
    'SENTINEL_PAGE_HTML',
    'SENTINEL_TOKEN_VALUE',
    'https://market.invalid/product/SENTINEL_PRODUCT_ID?query=secret',
    'SENTINEL_COOKIE_VALUE',
    'SENTINEL_RAW_EXCEPTION',
)


@dataclass(frozen=True)
class FakeDeadline:
    expires_at: float


class SecretPage:
    async def content(self) -> str:
        return (
            '<div class="g-recaptcha">SENTINEL_PAGE_HTML '
            'SENTINEL_TOKEN_VALUE SENTINEL_COOKIE_VALUE '
            'https://market.invalid/product/SENTINEL_PRODUCT_ID'
            '?query=secret</div>'
        )


class FailingHandler:
    def supports(self, detection: ChallengeDetection) -> bool:
        return True

    async def handle(
        self,
        page: SecretPage,
        detection: ChallengeDetection,
    ) -> None:
        raise RuntimeError(
            'SENTINEL_RAW_EXCEPTION SENTINEL_TOKEN_VALUE '
            'https://market.invalid/product/SENTINEL_PRODUCT_ID?query=secret'
        )


class FailingPage:
    async def content(self) -> str:
        raise RuntimeError(
            'SENTINEL_RAW_EXCEPTION SENTINEL_PAGE_HTML'
        )


class ChallengeLogRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_and_repr_exclude_page_and_exception_secrets(
        self,
    ) -> None:
        coordinator = ChallengeCoordinator(
            (FailingHandler(),),
            clock=lambda: 0.0,
        )

        with self.assertLogs(
            'src.captcha.coordinator',
            level='WARNING',
        ) as log:
            resolution = await coordinator.resolve(
                SecretPage(),
                deadline=FakeDeadline(5.0),
            )

        rendered = '\n'.join(
            (*log.output, repr(resolution), repr(coordinator))
        )
        for sentinel in SENTINELS:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, rendered)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )

    async def test_detection_error_log_excludes_raw_exception(self) -> None:
        coordinator = ChallengeCoordinator((), clock=lambda: 0.0)

        with self.assertLogs(
            'src.captcha.coordinator',
            level='WARNING',
        ) as log:
            resolution = await coordinator.resolve(
                FailingPage(),
                deadline=FakeDeadline(5.0),
            )

        rendered = '\n'.join(log.output)
        self.assertNotIn('SENTINEL_RAW_EXCEPTION', rendered)
        self.assertNotIn('SENTINEL_PAGE_HTML', rendered)
        self.assertIs(
            ChallengeResolution.CHALLENGE_UNSOLVABLE,
            resolution,
        )


if __name__ == '__main__':
    unittest.main()
