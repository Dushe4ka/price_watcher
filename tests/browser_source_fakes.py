from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from src.captcha.models import ChallengeResolution


@dataclass(frozen=True, slots=True)
class FakeNavigationResponse:
    status: int = 200


class FakePage:
    def __init__(
        self,
        *,
        html: str,
        evaluation: object = None,
        status: int = 200,
        redirect_url: str | None = None,
    ) -> None:
        self.url = 'about:blank'
        self.html = html
        self.evaluation = evaluation
        self.status = status
        self.redirect_url = redirect_url
        self.closed = False
        self.goto_urls: list[str] = []
        self.goto_timeouts: list[float] = []
        self.content_pages: list[FakePage] = []
        self.evaluation_pages: list[FakePage] = []
        self.expressions: list[str] = []
        self.handlers: dict[str, list[Callable[[Any], Any]]] = {}

    @property
    def main_frame(self) -> object:
        return self

    @property
    def frames(self) -> tuple[()]:
        return ()

    def is_closed(self) -> bool:
        return self.closed

    def on(self, event: str, handler: Callable[[Any], Any]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def close(self) -> None:
        self.closed = True

    async def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> FakeNavigationResponse:
        del wait_until
        self.goto_urls.append(url)
        self.goto_timeouts.append(timeout)
        self.url = self.redirect_url or url
        return FakeNavigationResponse(self.status)

    async def content(self) -> str:
        self.content_pages.append(self)
        return self.html

    async def evaluate(self, expression: str) -> object:
        self.evaluation_pages.append(self)
        self.expressions.append(expression)
        return self.evaluation

    async def title(self) -> str:
        return 'Synthetic marketplace page'

    async def wait_for_timeout(self, timeout: float) -> None:
        await asyncio.sleep(timeout / 1000)


class HangingContentPage(FakePage):
    def __init__(self, *, html: str) -> None:
        super().__init__(html=html)
        self.late_mutations = 0

    async def content(self) -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            if not self.closed:
                self.late_mutations += 1
            raise


class BrokenContentPage(FakePage):
    async def content(self) -> str:
        raise RuntimeError('synthetic-sensitive-browser-body')


class RedirectOnContentPage(FakePage):
    def __init__(
        self,
        *,
        html: str,
        redirect_after_call: int,
    ) -> None:
        super().__init__(html=html)
        self.redirect_after_call = redirect_after_call
        self.content_calls = 0

    async def content(self) -> str:
        self.content_calls += 1
        self.content_pages.append(self)
        if self.content_calls >= self.redirect_after_call:
            self.url = 'https://attacker.invalid/late-redirect'
        return self.html


class BrokenEvaluationPage(FakePage):
    async def evaluate(self, expression: str) -> object:
        del expression
        raise RuntimeError('synthetic DOM extraction drift')


class SequenceEvaluationPage(FakePage):
    def __init__(
        self,
        *,
        html: str,
        evaluations: list[object],
    ) -> None:
        super().__init__(html=html)
        self.evaluations = list(evaluations)

    async def evaluate(self, expression: str) -> object:
        self.evaluation_pages.append(self)
        self.expressions.append(expression)
        return self.evaluations.pop(0)


class SequencedStatusPage(FakePage):
    """Scripts status/HTML across repeated ``goto`` calls to the same URL.

    Models a self-resolving check: each successive navigation (including a
    poll-driven reload of the exact same URL) can hand back a different
    status/body pair, so a caller polling by re-navigating observes the
    scripted sequence in order and then holds on the last entry.
    """

    def __init__(
        self,
        *,
        statuses: list[int],
        htmls: list[str],
        evaluation: object = None,
    ) -> None:
        super().__init__(html=htmls[0], evaluation=evaluation)
        self._statuses = list(statuses)
        self._htmls = list(htmls)
        self.goto_calls = 0

    async def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> FakeNavigationResponse:
        del wait_until
        self.goto_urls.append(url)
        self.goto_timeouts.append(timeout)
        self.url = url
        index = min(self.goto_calls, len(self._statuses) - 1)
        self.html = self._htmls[min(self.goto_calls, len(self._htmls) - 1)]
        self.goto_calls += 1
        return FakeNavigationResponse(self._statuses[index])


class HangingActionPage(FakePage):
    def __init__(self, *, action: str, html: str = 'unused') -> None:
        super().__init__(html=html)
        self.action = action
        self.action_started = asyncio.Event()

    async def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> FakeNavigationResponse:
        if self.action != 'goto':
            return await super().goto(
                url,
                wait_until=wait_until,
                timeout=timeout,
            )
        self.goto_urls.append(url)
        self.action_started.set()
        await asyncio.Event().wait()
        raise AssertionError('unreachable')

    async def evaluate(self, expression: str) -> object:
        if self.action not in ('evaluate', 'fetch'):
            return await super().evaluate(expression)
        self.evaluation_pages.append(self)
        self.expressions.append(expression)
        self.action_started.set()
        await asyncio.Event().wait()
        raise AssertionError('unreachable')


class SequencedUrlPage(FakePage):
    def __init__(self, *, html: str, unsafe_after_reads: int) -> None:
        self._current_url = 'about:blank'
        self.url_reads = 0
        self.unsafe_after_reads = unsafe_after_reads
        super().__init__(html=html)

    @property
    def url(self) -> str:
        self.url_reads += 1
        if self.url_reads > self.unsafe_after_reads:
            return 'https://attacker.invalid/race'
        return self._current_url

    @url.setter
    def url(self, value: str) -> None:
        self._current_url = value


class FakeOzonStreamingPage(FakePage):
    _MAX_BYTES_RE = re.compile(r'const maxBytes = (\d+);')

    def __init__(
        self,
        *,
        body: str,
        status: int = 200,
        response_url: str = (
            'https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2'
        ),
        redirected: bool = False,
        response_type: str = 'basic',
        chunks: list[bytes] | None = None,
    ) -> None:
        super().__init__(html='<html>Ozon</html>')
        self.body = body
        self.fetch_status = status
        self.response_url = response_url
        self.redirected = redirected
        self.response_type = response_type
        self.chunks = chunks or [body.encode('utf-8')]
        self.body_reads = 0
        self.reader_cancelled = False
        self.manual_redirect_requested = False
        self.streaming_reader_requested = False

    async def evaluate(self, expression: str) -> object:
        self.evaluation_pages.append(self)
        self.expressions.append(expression)
        self.manual_redirect_requested = "redirect: 'manual'" in expression
        self.streaming_reader_requested = 'getReader()' in expression
        if not self.manual_redirect_requested:
            self.body_reads = len(self.chunks)
            return {
                'status': self.fetch_status,
                'url': self.response_url,
                'body': self.body,
            }
        if (
            self.redirected
            or self.response_type == 'opaqueredirect'
            or 300 <= self.fetch_status < 400
        ):
            return {'kind': 'redirect'}
        if not self.response_url.startswith('https://www.ozon.ru/'):
            return {'kind': 'unsafe_response'}
        if not 200 <= self.fetch_status < 300:
            return {
                'kind': 'status',
                'status': self.fetch_status,
                'url': self.response_url,
            }
        match = self._MAX_BYTES_RE.search(expression)
        max_bytes = int(match.group(1)) if match else 2_000_000
        total = 0
        for chunk in self.chunks:
            self.body_reads += 1
            total += len(chunk)
            if total > max_bytes:
                self.reader_cancelled = True
                return {'kind': 'too_large'}
        return {
            'kind': 'body',
            'status': self.fetch_status,
            'url': self.response_url,
            'body': self.body,
        }


class FakeManager:
    def __init__(
        self,
        page: FakePage,
        *,
        lease_error: Exception | None = None,
    ) -> None:
        self.page = page
        self.lease_error = lease_error
        self.marketplaces: list[str] = []

    @asynccontextmanager
    async def lease(self, marketplace: str) -> AsyncIterator[FakePage]:
        self.marketplaces.append(marketplace)
        if self.lease_error is not None:
            raise self.lease_error
        try:
            yield self.page
        finally:
            if not self.page.is_closed():
                await self.page.close()


class DecoyPageManager:
    """Manager owning two live pages that leases exactly one of them.

    The decoy is a real candidate: it is reachable from the same manager the
    source holds, and it is what ``page``/``pages[0]`` hand back. A source that
    read state from anything other than the leased Page would pick it up.
    """

    def __init__(self, leased: FakePage, decoy: FakePage) -> None:
        self.leased = leased
        self.decoy = decoy
        self.page = decoy
        self.pages = [decoy, leased]
        self.marketplaces: list[str] = []

    @asynccontextmanager
    async def lease(self, marketplace: str) -> AsyncIterator[FakePage]:
        self.marketplaces.append(marketplace)
        try:
            yield self.leased
        finally:
            if not self.leased.is_closed():
                await self.leased.close()


class CancellationSuppressingEnterManager:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.enter_started = asyncio.Event()
        self.release = asyncio.Event()

    @asynccontextmanager
    async def lease(self, marketplace: str) -> AsyncIterator[FakePage]:
        del marketplace
        self.enter_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self.release.wait()
        yield self.page


class CancellationSuppressingExitManager:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.exit_started = asyncio.Event()
        self.release = asyncio.Event()

    @asynccontextmanager
    async def lease(self, marketplace: str) -> AsyncIterator[FakePage]:
        del marketplace
        try:
            yield self.page
        finally:
            self.exit_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.release.wait()


class HangingClosePage(FakePage):
    def __init__(self, *, html: str) -> None:
        super().__init__(html=html)
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


class TimingOutNavigationPage(FakePage):
    """Navigation raises the browser's own timeout; closing then wedges.

    This separates the per-operation timeout branch from absolute deadline
    exhaustion: the deadline still has plenty of time left when `goto` gives
    up, so anything the timeout branch awaits is visible in wall-clock time.
    """

    def __init__(self, *, html: str = 'unused') -> None:
        super().__init__(html=html)
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> FakeNavigationResponse:
        del wait_until
        self.goto_urls.append(url)
        self.goto_timeouts.append(timeout)
        raise asyncio.TimeoutError('synthetic navigation timeout')

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


class NonClosingManager:
    """Lease that never closes the page, isolating source-side closure."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.marketplaces: list[str] = []

    @asynccontextmanager
    async def lease(self, marketplace: str) -> AsyncIterator[FakePage]:
        self.marketplaces.append(marketplace)
        yield self.page


class FakeCoordinator:
    def __init__(
        self,
        *resolutions: ChallengeResolution,
    ) -> None:
        self._resolutions = list(
            resolutions
            or (
                ChallengeResolution.NO_CHALLENGE,
                ChallengeResolution.NO_CHALLENGE,
            )
        )
        self.pages: list[FakePage] = []
        self.deadlines: list[object] = []

    async def resolve(
        self,
        page: FakePage,
        *,
        deadline: object,
    ) -> ChallengeResolution:
        self.pages.append(page)
        self.deadlines.append(deadline)
        if self._resolutions:
            return self._resolutions.pop(0)
        return ChallengeResolution.NO_CHALLENGE


class HangingCoordinator(FakeCoordinator):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def resolve(
        self,
        page: FakePage,
        *,
        deadline: object,
    ) -> ChallengeResolution:
        self.pages.append(page)
        self.deadlines.append(deadline)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError('unreachable')
