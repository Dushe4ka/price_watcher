from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import time
import unittest

from pydantic import ValidationError

from src.captcha.coordinator import ChallengeCoordinator
from src.captcha.models import (
    ChallengeDetection,
    ChallengeResolution,
    ChallengeType,
)
from src.captcha.smartcaptcha import (
    SmartCaptchaHandler,
    SmartCaptchaMode,
)
from src.core.config import Settings


FIXTURE = (
    Path(__file__).parent
    / 'fixtures'
    / 'challenges'
    / 'smartcaptcha-callback.html'
).read_text(encoding='utf-8')
CLEAN_HTML = '<!doctype html><html><body>marketplace content</body></html>'
TRUSTED_WIDGET_ID = 'market-widget_01'


def find_node_executable() -> str | None:
    """Use system Node or Playwright's pinned bundled runtime."""
    executable = shutil.which('node')
    if executable is not None:
        return executable
    import playwright

    node_name = 'node.exe' if os.name == 'nt' else 'node'
    bundled = Path(playwright.__file__).parent / 'driver' / node_name
    return str(bundled) if bundled.is_file() else None


NODE_EXECUTABLE = find_node_executable()


@dataclass(frozen=True)
class FakeDeadline:
    expires_at: float


def deadline(seconds: float = 0.1) -> FakeDeadline:
    return FakeDeadline(expires_at=time.monotonic() + seconds)


def smartcaptcha_detection() -> ChallengeDetection:
    return ChallengeDetection(challenge_type=ChallengeType.UNKNOWN)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        'db_dialect': 'postgresql',
        'db_driver': 'asyncpg',
        'secret': 'application-secret',
        'first_superuser_email': 'admin@example.invalid',
        'first_superuser_password': 'superuser-password',
        'postgres_user': 'postgres-user',
        'postgres_password': 'postgres-password',
        'postgres_db': 'price-watcher',
        'postgres_port': '5432',
        'postgres_host': 'localhost',
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeSmartCaptchaPage:
    def __init__(
        self,
        event: str = 'success',
        *,
        clear_after_success_read: int | None = 2,
        raw_error: str = '',
    ) -> None:
        self.event = event
        self.clear_after_success_read = clear_after_success_read
        self.raw_error = raw_error
        self.html = FIXTURE
        self.content_calls = 0
        self.evaluated: list[str] = []
        self.close_calls = 0
        self.closed = False
        self.cancelled = False
        self.late_mutations = 0
        self.release = asyncio.Event()

    async def content(self) -> str:
        self.content_calls += 1
        if (
            self.event == 'success'
            and self.clear_after_success_read is not None
            and self.content_calls >= self.clear_after_success_read
        ):
            return CLEAN_HTML
        return self.html

    async def evaluate(self, expression: str) -> str:
        self.evaluated.append(expression)
        if self.event == 'raise':
            raise RuntimeError(self.raw_error)
        if self.event != 'timeout':
            return self.event
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            await self.release.wait()
            if not self.closed:
                self.late_mutations += 1
            raise
        return 'success'

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class JavaScriptSmartCaptchaPage(FakeSmartCaptchaPage):
    def __init__(
        self,
        *,
        synchronous_event: str,
        execute_error: str = '',
        callback_payload: str = '',
    ) -> None:
        super().__init__('success')
        self.synchronous_event = synchronous_event
        self.execute_error = execute_error
        self.callback_payload = callback_payload
        self.trace: list[str] = []

    async def evaluate(self, expression: str) -> str:
        self.evaluated.append(expression)
        program = f"""
const trace = [];
const callbacks = new Map();
const synchronousEvent = {json.dumps(self.synchronous_event)};
const executeError = {json.dumps(self.execute_error)};
const callbackPayload = {json.dumps(self.callback_payload)};
globalThis.window = {{
    smartCaptcha: {{
        subscribe(widgetId, event, callback) {{
            trace.push(`subscribe:${{event}}`);
            callbacks.set(event, callback);
            if (event === synchronousEvent) {{
                callback(callbackPayload);
            }}
            return () => trace.push(`unsubscribe:${{event}}`);
        }},
        execute(widgetId) {{
            trace.push('execute');
            if (executeError) throw new Error(executeError);
        }},
    }},
}};
const solve = ({expression});
const status = await solve();
process.stdout.write(JSON.stringify({{status, trace}}));
"""
        process = await asyncio.create_subprocess_exec(
            NODE_EXECUTABLE,
            '--input-type=module',
            '--eval',
            program,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError('controlled JavaScript runtime failed')
        result = json.loads(stdout.decode('utf-8'))
        self.trace = result['trace']
        return result['status']


class SmartCaptchaHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_mode_fails_closed_without_javascript(self) -> None:
        page = FakeSmartCaptchaPage()

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.DISABLED,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual([], page.evaluated)

    async def test_missing_widget_id_fails_closed_without_javascript(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage()

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual([], page.evaluated)

    async def test_invalid_widget_id_fails_closed_without_javascript(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage()

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id='unsafe widget id; alert(1)',
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual([], page.evaluated)

    async def test_success_requires_same_page_challenge_disappearance(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage(
            'success',
            clear_after_success_read=3,
        )
        handler = SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        )

        result = await handler.solve(
            page,
            smartcaptcha_detection(),
            deadline(),
        )

        self.assertIs(ChallengeResolution.SOLVED, result)
        self.assertEqual(1, len(page.evaluated))
        self.assertGreaterEqual(page.content_calls, 3)

    async def test_uses_only_official_callbacks_with_trusted_widget_id(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage()

        await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        expression = page.evaluated[0]
        normalized_expression = ' '.join(expression.split())
        self.assertIn(f'"{TRUSTED_WIDGET_ID}"', expression)
        self.assertIn("'challenge-visible'", expression)
        self.assertIn("'javascript-error'", expression)
        self.assertIn("'network-error'", expression)
        self.assertIn("'success'", expression)
        self.assertIn("'token-expired'", expression)
        self.assertIn('api.subscribe( widgetId, event,', normalized_expression)
        self.assertIn('api.execute(widgetId)', expression)
        self.assertNotIn('querySelector', expression)

    async def test_success_callback_alone_is_not_accepted(self) -> None:
        page = FakeSmartCaptchaPage(
            'success',
            clear_after_success_read=None,
        )

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline(0.02))

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)

    async def test_visible_challenge_is_not_clicked_or_solved(self) -> None:
        page = FakeSmartCaptchaPage('challenge_visible')

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual(1, len(page.evaluated))

    async def test_javascript_error_fails_closed(self) -> None:
        page = FakeSmartCaptchaPage('javascript_error')

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)

    async def test_expired_callback_fails_closed(self) -> None:
        page = FakeSmartCaptchaPage('token_expired')

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)

    async def test_missing_page_api_fails_closed(self) -> None:
        page = FakeSmartCaptchaPage('api_unavailable')

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)

    async def test_raw_javascript_error_is_not_retained_or_exposed(
        self,
    ) -> None:
        sentinel = 'SENTINEL_TOKEN_AND_JAVASCRIPT_ERROR'
        page = FakeSmartCaptchaPage('raise', raw_error=sentinel)
        handler = SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        )

        result = await handler.solve(
            page,
            smartcaptcha_detection(),
            deadline(),
        )

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertNotIn(sentinel, repr(result))
        self.assertNotIn(sentinel, repr(handler))
        self.assertFalse(hasattr(handler, '__dict__'))

    async def test_timeout_closes_page_before_cancelled_work_can_mutate(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage('timeout')
        handler = SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        )

        result = await handler.solve(
            page,
            smartcaptcha_detection(),
            deadline(0.01),
        )
        await asyncio.wait_for(_wait_until(lambda: page.closed), 0.1)
        page.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertTrue(page.cancelled)
        self.assertEqual(1, page.close_calls)
        self.assertEqual(0, page.late_mutations)

    async def test_unrelated_detection_never_runs_smartcaptcha(self) -> None:
        page = FakeSmartCaptchaPage()
        detection = ChallengeDetection(
            challenge_type=ChallengeType.RECAPTCHA_V3,
        )

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, detection, deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual([], page.evaluated)

    async def test_coordinator_preserves_existing_unknown_semantics(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage()

        result = await ChallengeCoordinator([]).resolve(
            page,
            deadline=deadline(),
        )

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual([], page.evaluated)

    async def test_coordinator_delegates_unknown_on_same_page(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage()
        handler = SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        )

        result = await ChallengeCoordinator(
            [],
            smartcaptcha_handler=handler,
        ).resolve(page, deadline=deadline())

        self.assertIs(ChallengeResolution.SOLVED, result)
        self.assertEqual(1, len(page.evaluated))

    async def test_interactive_unknown_never_reaches_smartcaptcha(
        self,
    ) -> None:
        page = FakeSmartCaptchaPage()
        page.html = (
            '<div class="smart-captcha">'
            '<div data-challenge-type="slider"></div>'
            '</div>'
        )
        handler = SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        )

        result = await ChallengeCoordinator(
            [],
            smartcaptcha_handler=handler,
        ).resolve(page, deadline=deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual([], page.evaluated)

    @unittest.skipUnless(NODE_EXECUTABLE, 'Node.js runtime is unavailable')
    async def test_synchronous_terminal_callback_cleans_up_and_stops(
        self,
    ) -> None:
        page = JavaScriptSmartCaptchaPage(
            synchronous_event='challenge-visible',
        )

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertEqual(
            (
                'subscribe:challenge-visible',
                'unsubscribe:challenge-visible',
            ),
            tuple(page.trace),
        )

    @unittest.skipUnless(NODE_EXECUTABLE, 'Node.js runtime is unavailable')
    async def test_synchronous_success_cleans_every_subscription(
        self,
    ) -> None:
        page = JavaScriptSmartCaptchaPage(
            synchronous_event='success',
            callback_payload='SENTINEL_CALLBACK_TOKEN',
        )

        result = await SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        ).solve(page, smartcaptcha_detection(), deadline())

        self.assertIs(ChallengeResolution.SOLVED, result)
        self.assertEqual(1, page.trace.count('execute'))
        self.assertEqual(
            5,
            sum(item.startswith('subscribe:') for item in page.trace),
        )
        self.assertEqual(
            5,
            sum(item.startswith('unsubscribe:') for item in page.trace),
        )

    @unittest.skipUnless(NODE_EXECUTABLE, 'Node.js runtime is unavailable')
    async def test_execute_failure_overrides_synchronous_success(
        self,
    ) -> None:
        sentinel_token = 'SENTINEL_CALLBACK_TOKEN'
        sentinel_error = 'SENTINEL_RAW_JAVASCRIPT_ERROR'
        page = JavaScriptSmartCaptchaPage(
            synchronous_event='success',
            execute_error=sentinel_error,
            callback_payload=sentinel_token,
        )
        handler = SmartCaptchaHandler(
            SmartCaptchaMode.FRICTIONLESS,
            widget_id=TRUSTED_WIDGET_ID,
        )

        with self.assertNoLogs(
            'src.captcha.smartcaptcha',
            level='DEBUG',
        ):
            result = await handler.solve(
                page,
                smartcaptcha_detection(),
                deadline(),
            )

        rendered = '\n'.join((repr(result), repr(handler)))
        self.assertIs(ChallengeResolution.CHALLENGE_UNSOLVABLE, result)
        self.assertNotIn(sentinel_token, rendered)
        self.assertNotIn(sentinel_error, rendered)
        self.assertIsNone(getattr(result, '__cause__', None))
        self.assertIn('execute', page.trace)


class SmartCaptchaSettingsTests(unittest.TestCase):
    def test_smartcaptcha_defaults_are_disabled_and_unset(self) -> None:
        settings = make_settings()

        self.assertIs(SmartCaptchaMode.DISABLED, settings.smartcaptcha_mode)
        self.assertEqual('', settings.smartcaptcha_widget_id)

    def test_widget_id_configuration_rejects_untrusted_characters(
        self,
    ) -> None:
        for value in ('unsafe widget', '../widget', 'x' * 129):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    make_settings(smartcaptcha_widget_id=value)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0)


if __name__ == '__main__':
    unittest.main()
